"""Offline model of audited title arithmetic; never used by the game runtime."""
import struct


def signed32(value):
    value &= 0xffffffff
    return value-0x100000000 if value&0x80000000 else value


def correction_update(previous_peak, received_peak, skip_pacing=False, clock_ready=True):
    """0x16EFFC..0x16F304: retain the greater u32 peak, then halve or clear.

    Returns the retained queue peak and optional write to pacing +0x124.
    Clearing the queue peak does not itself clear the pacing field.
    """
    peak=max(previous_peak&0xffffffff,received_peak&0xffffffff)
    if not clock_ready or skip_pacing or peak<=512:
        return 0,None
    peak >>= 1
    return peak,peak if peak else None


def receive_sample(remote_sample_us, local_stamp_us, prior_peak=0):
    """0x16E148: signed positive u32 difference, then unsigned maximum."""
    delta=signed32(remote_sample_us-local_stamp_us)
    return max(prior_peak&0xffffffff,delta) if delta>0 else prior_peak&0xffffffff


def f32(value):
    return struct.unpack('!f',struct.pack('!f',value))[0]


def pacing_deadline(previous_us, interval_bits, correction_u32):
    """0x1A3C40: single-rounded FMA, then single-rounded correction add."""
    interval=struct.unpack('!f',struct.pack('!I',interval_bits))[0]
    return f32(f32(interval*1_000_000+f32(previous_us))+f32(correction_u32&0xffffffff))
