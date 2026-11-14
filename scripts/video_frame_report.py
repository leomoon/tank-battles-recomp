#!/usr/bin/env python3
"""Measure recorded timestamps and inspect every decoded window-video frame."""
import argparse
import json
from pathlib import Path
import subprocess

def changed_fraction(a,b):
    if a==b:return 0.0
    return sum(abs(a[i]-b[i])>24 or abs(a[i+1]-b[i+1])>24 or abs(a[i+2]-b[i+2])>24 for i in range(0,len(a),3))/(len(a)//3)

def report_video(path, region=None, viewport=(1280,720)):
    path=Path(path)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_packets','-show_streams','-show_entries','packet=pts_time:stream=width,height','-of','json',str(path)],text=True,timeout=60))
    stamps=sorted(float(f['pts_time']) for f in info['packets'])
    if len(stamps)<2 or any(b<a for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Missing or decreasing capture timestamps')
    width,height=info['streams'][0]['width'],info['streams'][0]['height']
    gaps=[b-a for a,b in zip(stamps,stamps[1:])]
    rate=(len(stamps)-1)/(stamps[-1]-stamps[0])
    distinct_rate=(len(set(stamps))-1)/(stamps[-1]-stamps[0])
    filters=[]
    crop=None
    if region:
        vw,vh=viewport;x,y,w,h=region
        if min(vw,vh,w,h)<=0 or min(x,y)<0 or x+w>vw or y+h>vh:
            raise ValueError('Invalid viewport region')
        if width*vh>height*vw:
            ah=height;aw=(height*vw+vh//2)//vh
        else:
            aw=width;ah=(width*vh+vw//2)//vw
        cx=(width-aw)//2+round(x*aw/vw);cy=(height-ah)//2+round(y*ah/vh)
        cw=round(w*aw/vw);ch=round(h*ah/vh)
        crop=[cx,cy,cw,ch]
        filters.append(f'crop={cw}:{ch}:{cx}:{cy}:exact=1')
    filters.append('scale=160:90:flags=area')
    # Passthrough is essential: do not create/drop frames to force a nominal rate.
    decoder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-i',str(path),'-vf',','.join(filters),'-pix_fmt','rgb24','-fps_mode','passthrough','-f','rawvideo','pipe:1'],stdout=subprocess.PIPE)
    size=160*90*3;previous=None;before_previous=None;count=0;changes=[];isolated=[];edges=[];excursions=[]
    try:
        while True:
            frame=decoder.stdout.read(size)
            if not frame:break
            if len(frame)!=size:raise ValueError('Truncated decoded frame')
            if previous is not None:
                difference=changed_fraction(previous,frame)
                # A guest frame can span several captured refreshes. Retain
                # incoming edges so duplicated bad frames do not hide a flash.
                edges=[e for e in edges if stamps[count]-stamps[e[0]]<=.25]
                if difference>.08:
                    for start,baseline in edges:
                        if count-start>1 and changed_fraction(baseline,frame)<.02:
                            excursions.append(dict(first_frame=start,return_frame=count,
                                seconds=stamps[start],duration_seconds=stamps[count]-stamps[start]))
                    edges.append((count,previous))
                if before_previous is not None and difference>.08 and changes[-1][0]>.08:
                    return_difference=changed_fraction(before_previous,frame)
                    if return_difference<.02:
                        isolated.append(dict(frame=count-1,seconds=stamps[count-1],incoming_fraction=changes[-1][0],outgoing_fraction=difference,return_fraction=return_difference))
                changes.append((difference,count))
            before_previous=previous;previous=frame;count+=1
    finally:
        decoder.stdout.close()
        if decoder.wait(timeout=30):raise ValueError('Video decoder failed')
    if count!=len(stamps):raise ValueError('Decoder did not retain every recorded frame')
    return dict(video=str(path),frames=count,width=width,height=height,measured_capture_fps=rate,distinct_timestamp_fps=distinct_rate,duplicate_timestamps=len(stamps)-len(set(stamps)),meets_30fps=distinct_rate>=30,max_gap_seconds=max(gaps),gaps_over_50ms=sum(g>.05 for g in gaps),region_in_native_viewport=region,window_crop=crop,
                largest_sampled_change=max(v for v,i in changes),largest_changes=[dict(fraction=v,frame=i,seconds=stamps[i]) for v,i in sorted(changes,reverse=True)[:10]],
                frame_changes=[dict(fraction=v,frame=i,seconds=stamps[i]) for v,i in changes],isolated_change_candidates=isolated,short_excursion_candidates=excursions,
                limitations='Every encoded frame is decoded without frame-rate conversion. Spatial samples detect large temporal changes; full video remains necessary for other artifacts. Expected animation and scene transitions are not automatically flicker. Window capture cadence is not game FPS or simulation speed.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('video',type=Path);p.add_argument('--region',type=int,nargs=4);p.add_argument('--output',type=Path)
    a=p.parse_args();r=report_video(a.video,a.region);out=a.output or a.video.with_suffix('.report.json');out.write_text(json.dumps(r,indent=2)+'\n')
    summary={k:r[k] for k in ('frames','measured_capture_fps','distinct_timestamp_fps','max_gap_seconds','largest_sampled_change')}
    summary['isolated_change_candidates']=len(r['isolated_change_candidates'])
    summary['short_excursion_candidates']=len(r['short_excursion_candidates'])
    print(json.dumps(summary,indent=2))
    if not r['meets_30fps']:raise SystemExit('Recording below 30 fps')


if __name__=='__main__':main()
