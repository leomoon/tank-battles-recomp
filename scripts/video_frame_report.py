#!/usr/bin/env python3
"""Measure recorded timestamps and inspect every decoded window-video frame."""
import argparse
import json
from pathlib import Path
import subprocess


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
    size=160*90*3;previous=None;count=0;changes=[]
    try:
        while True:
            frame=decoder.stdout.read(size)
            if not frame:break
            if len(frame)!=size:raise ValueError('Truncated decoded frame')
            if previous is not None:
                changed=sum(max(abs(a-b) for a,b in zip(previous[i:i+3],frame[i:i+3]))>24 for i in range(0,size,3))
                changes.append((changed/(size//3),count))
            previous=frame;count+=1
    finally:
        decoder.stdout.close()
        if decoder.wait(timeout=30):raise ValueError('Video decoder failed')
    if count!=len(stamps):raise ValueError('Decoder did not retain every recorded frame')
    return dict(video=str(path),frames=count,width=width,height=height,measured_capture_fps=rate,distinct_timestamp_fps=distinct_rate,duplicate_timestamps=len(stamps)-len(set(stamps)),meets_30fps=distinct_rate>=30,max_gap_seconds=max(gaps),gaps_over_50ms=sum(g>.05 for g in gaps),region_in_native_viewport=region,window_crop=crop,
                largest_sampled_change=max(v for v,i in changes),largest_changes=[dict(fraction=v,frame=i,seconds=stamps[i]) for v,i in sorted(changes,reverse=True)[:10]],
                limitations='Every encoded frame is decoded without frame-rate conversion. Spatial samples detect large temporal changes; full video remains necessary for other artifacts. Expected animation and scene transitions are not automatically flicker. Window capture cadence is not game FPS or simulation speed.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('video',type=Path);p.add_argument('--region',type=int,nargs=4);p.add_argument('--output',type=Path)
    a=p.parse_args();r=report_video(a.video,a.region);out=a.output or a.video.with_suffix('.report.json');out.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:r[k] for k in ('frames','measured_capture_fps','max_gap_seconds','largest_sampled_change')},indent=2))
    if not r['meets_30fps']:raise SystemExit('Recording below 30 fps')


if __name__=='__main__':main()
