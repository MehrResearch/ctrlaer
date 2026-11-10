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
    """Return a hexadecimal SHA-256 of uint32 words encoded little endian.

    Hashes are independent of the host's byte order. Input words must fit in four
    unsigned bytes. Processing uses bounded chunks without a second full buffer.
    """
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
    """Return firmware/board/clock identity, or desktop Python identity offline.

    MicroPython results include implementation/version, full firmware string,
    `os.uname()`, board name and current clock in Hz. Desktop results include
    `execution='offline'`, implementation and Python version. No board is opened
    or contacted by the desktop function.
    """
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
    """Save a finite compiled experiment and optional capture/status as JSON.

    `path` is a filesystem path, `program` a validated `CompiledProgram`. Optional
    `run` contributes its `status()`, `trace` its exact packed samples, and
    `results`/`metadata` user dictionaries. `device` overrides `device_info()`
    when saving data from another board. User metadata must be JSON serialisable.

    Writes `ctrlaer.experiment/v1` with little-endian base64 buffers and SHA-256
    fingerprints. Buffer encoding is streamed rather than building a full list
    or base64 string in device RAM. A sibling `.tmp` is closed/synced before
    renaming to the destination; filesystem/power-loss guarantees depend on the
    platform. Errors remove the temporary file when possible and propagate.
    Returns the destination path as a string.

    Save after playback stops or during a deliberate hardware-gated pause:
    validation, status collection, capture summarisation and filesystem writes
    can block the interpreter. An unbounded future is not a `CompiledProgram`;
    analyse a separate finite window when a packet artifact is required.
    """
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
    """Load/validate a `ctrlaer.experiment/v1` record; return its decoded dictionary.

    `program` becomes a validated `CompiledProgram`; optional `capture` becomes a
    `Trace`. Encoded buffer fields are replaced by those objects. Parameters,
    device, execution status, results and user metadata remain in the dictionary.
    Buffer lengths/hashes and the duplicated parameter binding are checked.
    Unsupported/corrupt data raises `ValueError`; I/O/JSON errors propagate.

    Loading holds the JSON and decoded buffers in memory, so large records are
    best opened on desktop Python. This function does not execute a waveform or
    rerun its original generator.
    """
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
