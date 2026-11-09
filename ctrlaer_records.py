"""Versioned experiment records with exact program/capture buffers and hashes.

Writing is streamed in small chunks so a device need not construct a second large
list/string containing its DMA buffers. Loading large records is best done on the
desktop. No hardware import is needed for offline records or inspection.
"""
import json
from array import array
from ctrlaer_tools import program_to_dict, program_from_dict
from ctrlaer_capture import Trace


def _chunks(words):
    chunk = bytearray()
    for word in words:
        chunk.extend(int(word).to_bytes(4, "little"))
        # Multiple of three bytes allows concatenation of base64 chunks.
        if len(chunk) == 768:
            yield chunk
            chunk = bytearray()
    if chunk:
        yield chunk


def fingerprint(words):
    import hashlib
    import binascii
    digest = hashlib.sha256()
    for chunk in _chunks(words):
        digest.update(chunk)
    return binascii.hexlify(digest.digest()).decode()


def _write_buffer(stream, words):
    import binascii
    stream.write('{"encoding":"u32le-base64","count":%d,"sha256":"%s","data":"' %
                 (len(words), fingerprint(words)))
    for chunk in _chunks(words):
        stream.write(binascii.b2a_base64(chunk).decode().strip())
    stream.write('"}')


def _read_buffer(data):
    import binascii
    if data.get("encoding") != "u32le-base64":
        raise ValueError("unsupported record buffer encoding")
    raw = binascii.a2b_base64(data["data"])
    if len(raw) != data["count"] * 4:
        raise ValueError("record buffer length is incorrect")
    words = array("I")
    for offset in range(0, len(raw), 4):
        words.append(int.from_bytes(raw[offset:offset + 4], "little"))
    if fingerprint(words) != data["sha256"]:
        raise ValueError("record buffer hash mismatch")
    return words


def device_info():
    import sys
    implementation = sys.implementation
    if implementation.name != "micropython":
        return {"execution": "offline", "implementation": implementation.name,
                "python_version": sys.version}
    import machine
    import os
    return {"execution": "device", "implementation": implementation.name,
            "version": list(implementation.version),
            "firmware": sys.version, "uname": list(os.uname()),
            "board": implementation._machine, "clock_hz": machine.freq()}


def save_record(path, program, run=None, trace=None, results=None, metadata=None, device=None):
    """Atomically save parameters, exact buffers, device version and results."""
    import os
    program.validate()
    path = str(path)
    temporary = path + '.tmp'
    header = {"schema": "ctrlaer.experiment/v1", "parameters": program.parameters,
              "device": device_info() if device is None else device,
              "execution": None if run is None else run.status(),
              "results": {} if results is None else results,
              "metadata": {} if metadata is None else metadata,
              "program": program_to_dict(program, include_words=False)}
    try:
        with open(temporary, 'w') as stream:
            # Remove the final brace and append packed buffers without copying
            # them into the JSON encoder's object graph.
            prefix = json.dumps(header)
            stream.write(prefix[:-1])
            stream.write(',"program_buffer":')
            _write_buffer(stream, program.words)
            if trace is not None:
                stream.write(',"capture":')
                json.dump(trace.summary(), stream)
                stream.write(',"capture_buffer":')
                _write_buffer(stream, trace.words)
            stream.write('}\n')
        if hasattr(os, 'sync'):
            os.sync()
        os.rename(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return path


def load_record(path):
    """Load and validate a record, returning decoded program and Trace objects."""
    with open(path) as stream:
        data = json.load(stream)
    if data.get('schema') != 'ctrlaer.experiment/v1':
        raise ValueError("unsupported experiment record schema")
    program = program_from_dict(data['program'], _read_buffer(data.pop('program_buffer')))
    data['program'] = program
    if data['parameters'] != program.parameters:
        raise ValueError("record parameters disagree with compiled program")
    if 'capture' in data:
        capture = data['capture']
        words = _read_buffer(data.pop('capture_buffer'))
        data['capture'] = Trace(words, capture['n_pins'], capture['samples'],
                                capture['sample_hz'], capture['names'], capture['metadata'])
    return data
