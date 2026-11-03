"""Versioned, bounded media protocol shared by native tests and browser capture."""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct


class ProtocolError(ValueError):
    pass


# Wire: version:u8, sequence:u32, media_start_seconds:f64, little-endian PCM16.
# One fixed format avoids trusting per-packet sample-rate/channel declarations.
HEADER = struct.Struct('<BId')
SAMPLE_RATE = 16_000
MAX_PACKET_SAMPLES = 8_000


@dataclass(frozen=True)
class AudioPacket:
    sequence: int
    start: float
    pcm: bytes

    @property
    def end(self) -> float:
        return self.start + len(self.pcm) / (SAMPLE_RATE * 2)

    def encode(self) -> bytes:
        return HEADER.pack(1, self.sequence, self.start) + self.pcm

    @classmethod
    def decode(cls, data: bytes) -> 'AudioPacket':
        if len(data) < HEADER.size + 2 or len(data) > HEADER.size + MAX_PACKET_SAMPLES * 2:
            raise ProtocolError('PCM packet is empty or exceeds 500 ms')
        version, sequence, start = HEADER.unpack_from(data)
        if version != 1 or not math.isfinite(start) or start < 0 or start > 7 * 86400:
            raise ProtocolError('Unsupported version or invalid media clock')
        pcm = bytes(data[HEADER.size:])
        if len(pcm) % 2:
            raise ProtocolError('Partial PCM16 sample')
        return cls(sequence, start, pcm)


class CaptureClock:
    """Reject transport loss rather than concealing it as successful dubbing."""

    def __init__(self):
        self.next_sequence = 0
        self.end = None

    def accept(self, packet: AudioPacket):
        if packet.sequence != self.next_sequence:
            raise ProtocolError(f'Audio sequence gap/duplicate: expected {self.next_sequence}, received {packet.sequence}')
        if self.end is not None and abs(packet.start - self.end) > 0.004:
            raise ProtocolError('Non-contiguous source audio clock; restart the session after a seek or discontinuity')
        self.end = packet.end
        self.next_sequence += 1
