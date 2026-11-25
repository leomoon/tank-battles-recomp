"""Check the title's numeric clock-calibration observations without changing them."""
import argparse
import json
from pathlib import Path
from online_pacing_model import signed32


def report(path):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    header=rows[0]
    if header.get('schema')!=1 or header.get('clock')!='host_monotonic_ns':
        raise ValueError('Unsupported clock trace schema or clock')
    if header['events']!=len(rows)-1:
        raise ValueError('Incomplete clock trace')
    events=sorted(rows[1:],key=lambda e:e['ns'])
    if any(not header['start_ns']<=e['ns']<=header['end_ns'] for e in events):
        raise ValueError('Clock event outside trace window')
    series=[]
    active={}
    for event in events:
        if event['kind']!=10:continue
        count=event['local_sequence']
        key=(event['object'],event['socket'])
        if count==1 or key not in active:
            current=dict(transport=key[0],manager=key[1],samples=[],total_rtt=0,continuous=count==1)
            active[key]=current
            series.append(current)
        current=active[key]
        current['continuous'] &= count==len(current['samples'])+1
        current['total_rtt']+=event['result_u32']
        rtt=event['result_u32']
        # Time differences wrap, whereas the epoch is a full RTC tick value.
        # Compare offsets as signed differences within a half-u32 clock period.
        lower=signed32(event['interval_bits']-event['observed_guest_us'])
        upper=lower+rtt
        adjustment=signed32(event['correction_u32'])
        midpoint=signed32(event['interval_bits']-event['observed_guest_us']+rtt//2)
        expected=None
        if current['continuous'] and count:
            expected=signed32(event['interval_bits']-event['observed_guest_us']+
                              (current['total_rtt']//count)//2)
        comparable=rtt<0x80000000 and upper<0x80000000
        current['samples'].append(dict(sample=count,rtt_us=rtt,
            adjustment_us=adjustment,current_sample_midpoint_us=midpoint,
            midpoint_difference_us=signed32(adjustment-midpoint),
            response_offset_lower_us=lower,response_offset_upper_us=upper,
            bounds_comparable=comparable,
            inside_current_response_bounds=lower<=adjustment<=upper if comparable else None,
            original_arithmetic_matches=adjustment==expected if expected is not None else None))
    for item in series:
        item.pop('total_rtt')
        item['complete_ten_samples']=bool(item['continuous'] and len(item['samples'])==10)
    return dict(header=header,lossless=header['dropped']==0,
                usable_timeline=bool(header.get('complete') and header['dropped']==0),series=series,
                scope='Observed calibration arithmetic only. Response bounds assume nonnegative path delays and stable clocks during the exchange; they are not a one-way latency or FPS estimate.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace');parser.add_argument('--json',type=Path)
    args=parser.parse_args();result=report(args.trace)
    output=json.dumps(result,indent=2)+'\n'
    if args.json:args.json.write_text(output)
    print(output,end='')
