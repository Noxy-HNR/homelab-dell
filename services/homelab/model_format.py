"""Bounded GGUF v2/v3 structural inspection without loading tensor data.

Tensor block sizes follow ggml/llama.cpp's GGUF format. Unsupported new
tensor types are rejected instead of pretending their files are complete.
"""
import math
import os
import struct


# GGML type -> (values per block, bytes per block).
BLOCKS = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20),
    6: (32, 22), 7: (32, 24), 8: (32, 34), 9: (32, 36),
    10: (256, 84), 11: (256, 110), 12: (256, 144),
    13: (256, 176), 14: (256, 210), 15: (256, 292),
    16: (256, 66), 17: (256, 74), 18: (256, 98),
    19: (256, 50), 20: (32, 18), 21: (256, 110),
    22: (256, 82), 23: (256, 136), 24: (1, 1), 25: (1, 2),
    26: (1, 4), 27: (1, 8), 28: (1, 8), 29: (256, 56),
    30: (1, 2), 34: (256, 54), 35: (256, 66), 39: (32, 17),
}
SCALARS = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i',
           6: 'f', 7: '?', 10: 'Q', 11: 'q', 12: 'd'}


def inspect_gguf(stream):
    """Return a small metadata dict; raise ValueError for incomplete/unknown data."""
    size = os.fstat(stream.fileno()).st_size
    metadata_limit = min(size, 256 * 1024 * 1024)

    def read(count):
        if count < 0 or stream.tell() + count > metadata_limit:
            raise ValueError('GGUF header exceeds the file or metadata limit')
        data = stream.read(count)
        if len(data) != count:
            raise ValueError('Truncated GGUF header')
        return data

    def unpack(fmt):
        return struct.unpack('<' + fmt, read(struct.calcsize('<' + fmt)))[0]

    def skip(count):
        if count < 0 or stream.tell() + count > metadata_limit:
            raise ValueError('Truncated GGUF metadata')
        stream.seek(count, 1)

    def string(keep=False):
        count = unpack('Q')
        if keep:
            if count > 4096:
                raise ValueError('GGUF label is too long')
            return read(count).decode('utf-8', errors='replace')
        skip(count)
        return None

    def value(kind, keep=False):
        if kind in SCALARS:
            return unpack(SCALARS[kind])
        if kind == 8:
            return string(keep)
        if kind == 9:
            element_type, count = unpack('I'), unpack('Q')
            if count > 2_000_000 or element_type == 9:
                raise ValueError('Unsupported GGUF metadata array')
            if element_type in SCALARS:
                skip(count * struct.calcsize('<' + SCALARS[element_type]))
            elif element_type == 8:
                for _ in range(count):
                    string()
            else:
                raise ValueError('Unknown GGUF metadata type')
            return None
        raise ValueError('Unknown GGUF metadata type')

    if read(4) != b'GGUF':
        raise ValueError('File is not a little-endian GGUF')
    version, tensor_count, metadata_count = unpack('I'), unpack('Q'), unpack('Q')
    if version not in (2, 3) or not 0 < tensor_count <= 100_000 or metadata_count > 100_000:
        raise ValueError('Unsupported GGUF header or tensor count')
    selected = {}
    wanted = {'general.name', 'general.architecture', 'general.file_type',
              'general.alignment', 'split.count', 'split.no'}
    for _ in range(metadata_count):
        key = string(True)
        val = value(unpack('I'), key in wanted)
        if key in wanted:
            selected[key] = val
    alignment = selected.get('general.alignment', 32)
    if not isinstance(alignment, int) or alignment < 1 or alignment > 1024 * 1024 or alignment & (alignment - 1):
        raise ValueError('Invalid GGUF tensor alignment')
    tensor_ends = []
    for _ in range(tensor_count):
        string()
        ndim = unpack('I')
        if not 1 <= ndim <= 4:
            raise ValueError('Invalid GGUF tensor dimensions')
        dimensions = [unpack('Q') for _ in range(ndim)]
        kind, offset = unpack('I'), unpack('Q')
        if kind not in BLOCKS:
            raise ValueError(f'Unsupported GGUF tensor type {kind}')
        block, width = BLOCKS[kind]
        elements = math.prod(dimensions)
        if min(dimensions) == 0 or dimensions[0] % block or offset % alignment:
            raise ValueError('Invalid GGUF tensor shape or offset')
        tensor_ends.append(offset + elements // block * width)
    data_start = (stream.tell() + alignment - 1) // alignment * alignment
    if data_start + max(tensor_ends) > size:
        raise ValueError('GGUF tensor data is incomplete')
    return {'gguf_version': version, 'tensor_count': tensor_count,
            'metadata': {'name': selected.get('general.name'),
                         'architecture': selected.get('general.architecture'),
                         'quantization': selected.get('general.file_type'),
                         'split_count': selected.get('split.count', 1),
                         'split_index': selected.get('split.no', 0)}}
