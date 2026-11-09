"""Private fixed-point edge generation and packet scanning.

The device kernels use Viper and allocate nothing inside their loops. The desktop
fallback executes the same arithmetic, including 32-bit casts, for reproducible
previews and tests. Phase uses Q32 cycles; frequency uses Q56 cycles per system
clock. At a chirp reversal the integrated phase is anchored to the exact leg
integral, preventing accumulated period-rounding drift.
"""
from array import array
from math import floor, sqrt

try:
    import micropython
except ImportError:
    from types import FunctionType

    def _unsigned(value):
        return value & 0xffffffff

    def _signed(value):
        value &= 0xffffffff
        return value - 0x100000000 if value & 0x80000000 else value

    def _view(buffer):
        view = memoryview(buffer)
        return view.cast('I') if view.format != 'I' else view

    ptr32 = object
    uint = object

    def viper(function):
        scope = dict(function.__globals__, int=_signed, uint=_unsigned, ptr32=_view)
        return FunctionType(function.__code__, scope, function.__name__)

    class _HostEmitter:
        viper = staticmethod(viper)

    micropython = _HostEmitter()


@micropython.viper
def _chirp_edges(p: ptr32, out: ptr32, n: int) -> int:
    f = int(p[0]); frac = int(p[1]); slope = int(p[2])
    left = int(p[3]); leg = int(p[4]); lo = int(p[5]); hi = int(p[6])
    boundary = uint(p[7]); leg_phase = uint(p[8]); acc = uint(p[9])
    target = uint(p[10]); duty = uint(p[11]); high = int(p[12])
    time = int(p[13]); direction = int(p[14])
    lo_frac = int(p[16]); hi_frac = int(p[17]); dead = int(p[19])
    slope_extra = int(p[25]); freq_extra = int(p[26])
    lo_extra = int(p[27]); hi_extra = int(p[28])
    i = 0
    if int(p[18]) != 0 and n > 0:
        out[0] = uint(time + dead)
        p[18] = uint(0)
        i = 1
    while i < n:
        wanted = uint(target - acc)
        dt = int((wanted >> 1) + uint(f >> 2)) // (f >> 1)
        k = 0
        while k < 4:
            if dt < left:
                step_extra = slope_extra * dt
                raw = slope * dt + (step_extra >> 8)
                di = raw >> 16; df = raw & 65535
                advance = uint(f * dt + (di * dt >> 1) +
                               (df * dt >> 17) + (frac * dt >> 16))
                raw += frac + (((step_extra & 255) + freq_extra) >> 8)
                nf_extra = (step_extra + freq_extra) & 255
                nf = f + (raw >> 16); nf_frac = raw & 65535
            else:
                remain = dt - left
                start = hi if direction == 0 else lo
                start_frac = hi_frac if direction == 0 else lo_frac
                start_extra = hi_extra if direction == 0 else lo_extra
                ns = -slope - (1 if slope_extra else 0)
                ns_extra = (256 - slope_extra) & 255
                step_extra = ns_extra * remain
                raw = ns * remain + (step_extra >> 8)
                di = raw >> 16; df = raw & 65535
                after = uint(start * remain + (di * remain >> 1) +
                             (df * remain >> 17) + (start_frac * remain >> 16))
                advance = uint(uint(boundary - acc) + after)
                raw += start_frac + (((step_extra & 255) + start_extra) >> 8)
                nf_extra = (step_extra + start_extra) & 255
                nf = start + (raw >> 16); nf_frac = raw & 65535
            difference = int(wanted - advance)
            half = nf >> 1
            if difference > half:
                dt += 1 if difference < nf + half else (difference + half) // nf
            elif difference < -half:
                dt -= 1 if -difference < nf + half else (-difference + half) // nf
            else:
                break
            k += 1
        if k == 4:
            return -1  # Caller must never play an unconverged edge.
        time += dt
        out[i] = uint(time + (dead if high == 0 else 0))
        i += 1
        f = nf; frac = nf_frac; freq_extra = nf_extra
        if dt < left:
            left -= dt
            acc = uint(acc + advance)
        else:
            left = leg - (dt - left)
            slope = -slope - (1 if slope_extra else 0)
            slope_extra = (256 - slope_extra) & 255
            direction = 1 - direction
            acc = uint(boundary + after)
            boundary = uint(boundary + leg_phase)
        target = uint(target + duty) if high == 0 else uint(target - duty)
        high = 1 - high
    p[0] = uint(f); p[1] = uint(frac); p[2] = uint(slope); p[3] = uint(left)
    p[7] = boundary; p[9] = acc; p[10] = target; p[12] = uint(high)
    p[13] = uint(time); p[14] = uint(direction)
    p[25] = uint(slope_extra); p[26] = uint(freq_extra)
    return i


@micropython.viper
def _pulse_edges(p: ptr32, out: ptr32, n: int) -> int:
    time = int(p[13]); fraction = uint(p[24]); high = int(p[12])
    high_ticks = int(p[20]); high_fraction = uint(p[21])
    low_ticks = int(p[22]); low_fraction = uint(p[23]); dead = int(p[19])
    i = 0
    while i < n:
        out[i] = uint(time + int(fraction >> 31) + (dead if high == 0 else 0))
        previous = fraction
        if high == 0:
            time += high_ticks
            fraction = uint(fraction + high_fraction)
        else:
            time += low_ticks
            fraction = uint(fraction + low_fraction)
        if fraction < previous:
            time += 1
        high = 1 - high
        i += 1
    p[12] = uint(high); p[13] = uint(time); p[24] = fraction
    return i


@micropython.viper
def _scan_outputs(words: ptr32, begin: int, end: int, n_pins: int,
                  pulses: ptr32, state: ptr32) -> int:
    """Scan an OUT packet run; state is elapsed low/high, mask, error code."""
    position = begin
    elapsed = uint(state[0]); upper = uint(state[1]); previous = uint(state[2])
    instruction = uint(24576 | n_pins)
    limit = uint((1 << n_pins) - 1)
    while position < end:
        command = uint(words[position])
        if command & uint(65535) != instruction:
            break
        mask = command >> 16
        if mask > limit:
            state[3] = uint(1)
            break
        rising = mask & ~previous
        pin = 0
        while rising != uint(0):
            if rising & uint(1):
                pulses[pin] += uint(1)
            pin += 1
            rising >>= 1
        previous = mask
        old = elapsed
        elapsed = uint(elapsed + uint(words[position + 1]))
        if elapsed < old:
            upper += uint(1)
        old = elapsed
        elapsed = uint(elapsed + uint(6))
        if elapsed < old:
            upper += uint(1)
        position += 2
    state[0] = elapsed; state[1] = upper; state[2] = previous
    return position


@micropython.viper
def _pack_edges(ticks: ptr32, count: int, output: ptr32, config: ptr32) -> int:
    """Pack complete edge intervals; leave the last interval for coalescing."""
    total = int(config[0]); offset = int(config[1]); shift = int(config[8])
    first = int(config[2]); second = int(config[3]); mask = int(config[4])
    instruction = int(config[7]); previous = 0; position = 0; i = 0
    while i < count:
        tick = int(ticks[i]) - offset + shift
        next_mask = first if i & 1 == 0 else second
        i += 1
        if tick <= 0:
            mask = next_mask
            continue
        if tick >= total:
            break
        duration = tick - previous
        if duration < 6:
            return -1
        output[position] = uint(instruction | mask << 16)
        output[position + 1] = uint(duration - 6)
        position += 2
        previous = tick
        mask = next_mask
    if i == count and tick < total:
        return -2
    config[5] = uint(mask); config[6] = uint(total - previous)
    return position


@micropython.viper
def _copy_shift(destination: ptr32, dest_start: int, source: ptr32,
                source_start: int, count: int, shift: int):
    i = 0
    while i < count:
        destination[dest_start + i] = uint(int(source[source_start + i]) + shift)
        i += 1


def words(count):
    """Allocate a zeroed u32 buffer with one bounded temporary allocation."""
    return array('I', bytearray(count * 4))


def _ratio(numerator, denominator):
    """Round a signed rational to the nearest integer, ties toward +infinity."""
    return (2 * numerator + denominator) // (2 * denominator)


class CarrierEdges:
    """Internal resumable edge cursor; ``create`` declines unsupported ranges.

    fill_into returns ticks relative to ``origin``. It preserves phase and the
    pending next edge between calls, including chirp reversals inside a block.
    The last returned edge may lie beyond a caller's chosen duration: the caller
    clips playback, rather than discarding that edge from a continuing cursor.
    """
    def __init__(self, state, kernel, clock_hz):
        self.state, self.kernel, self.clock_hz = state, kernel, clock_hz
        self.origin = 0
        self._period = self._period_edges = 0
        self._cache = None
        self._cache_position = 0

    @classmethod
    def create(cls, command, clock_hz, offset_seconds=0):
        frequency = command.freq
        end = frequency if command.end_freq is None else command.end_freq
        duty, phase = command.duty, command.phase
        if not (0.02 <= duty <= .98 and 2000 <= min(frequency, end)
                and max(frequency, end) <= clock_hz / 32):
            return None
        # Multiplication by a power of two preserves the input float's binary
        # value. All subsequent setup arithmetic uses exact Python integers.
        f0 = round(frequency * 65536); f1 = round(end * 65536)
        high_phase = round(duty * 4294967296)
        first_phase = round(phase * 4294967296)
        if command.complement:
            first_phase += high_phase
            high_phase = 4294967296 - high_phase
        dead = round(command.dead_time_ns * clock_hz / 1e9)
        state = [0] * 29
        state[11], state[19] = high_phase, dead
        if command.end_freq is None or frequency == end:
            period_fp = clock_hz * 65536 * 4294967296 // f0
            first_fp = clock_hz * 65536 * first_phase // f0
            if offset_seconds:
                cycles = floor(offset_seconds * frequency - phase) - 1
                cycles = max(0, cycles)
                first_fp += cycles * period_fp
            high_fp = clock_hz * 65536 * high_phase // f0
            low_fp = period_fp - high_fp
            state[13], state[24] = first_fp >> 32, first_fp & 0xffffffff
            state[20], state[21] = high_fp >> 32, high_fp & 0xffffffff
            state[22], state[23] = low_fp >> 32, low_fp & 0xffffffff
            kernel = _pulse_edges
        else:
            leg_exact = command.sweep_ms * clock_hz / 1000
            leg = round(leg_exact)
            if leg <= 0:
                return None
            maximum_step = int(clock_hz / min(frequency, end)) + 2
            slope = _ratio((f1 - f0) << 40, clock_hz * leg)
            if (leg < maximum_step * 4 or leg >= 0x40000000
                    or abs(leg - leg_exact) > 1e-5
                    or abs(slope >> 8) * maximum_step >= 0x70000000
                    or maximum_step * 65535 >= 0x40000000):
                return None
            leg_phase = _ratio((f0 + f1) * leg << 15, clock_hz)
            cycles_per_leg = (frequency + end) * command.sweep_ms / 2000
            if offset_seconds:
                part = floor(offset_seconds * clock_hz / leg)
                local = offset_seconds - part * leg / clock_hz
                start = end if part % 2 else frequency
                acceleration = (frequency - end if part % 2 else end - frequency) * clock_hz / leg
                position = part * cycles_per_leg + start * local + .5 * acceleration * local * local
                cycle = max(0, floor(position - phase) - 1)
                first_phase += cycle * 4294967296
            cycles = first_phase / 4294967296
            part = floor(cycles / cycles_per_leg)
            remaining = cycles - part * cycles_per_leg
            start = end if part % 2 else frequency
            acceleration = (frequency - end if part % 2 else end - frequency) * clock_hz / leg
            first_tick = round((part * leg / clock_hz + 2 * remaining /
                                (start + sqrt(start * start + 2 * acceleration * remaining))) * clock_hz)
            part, local_tick = divmod(first_tick, leg)
            ascending = part % 2 == 0
            base_frequency = f0 if ascending else f1
            sign_slope = slope if ascending else -slope
            base_fp = _ratio(base_frequency << 40, clock_hz)
            at_fp = base_fp + sign_slope * local_tick
            integrated = ((base_frequency * local_tick << 16) // clock_hz +
                          (((f1 - f0 if ascending else f0 - f1) * local_tick * local_tick << 15) // (leg * clock_hz)))
            state[0], state[1], state[26] = at_fp >> 24, (at_fp >> 8) & 65535, at_fp & 255
            state[2], state[3], state[4] = (sign_slope >> 8) & 0xffffffff, leg - local_tick, leg
            state[25] = sign_slope & 255
            lo_fp = _ratio(f0 << 40, clock_hz); hi_fp = _ratio(f1 << 40, clock_hz)
            state[5], state[16], state[27] = lo_fp >> 24, (lo_fp >> 8) & 65535, lo_fp & 255
            state[6], state[17], state[28] = hi_fp >> 24, (hi_fp >> 8) & 65535, hi_fp & 255
            state[7], state[8] = ((part + 1) * leg_phase) & 0xffffffff, leg_phase & 0xffffffff
            state[9] = (part * leg_phase + integrated) & 0xffffffff
            state[10] = (first_phase + high_phase) & 0xffffffff
            state[12], state[13], state[14], state[18] = 1, first_tick, part % 2, 1
            kernel = _chirp_edges
        if state[13] >= 0x40000000:
            return None
        result = cls(array('I', state), kernel, clock_hz)
        if kernel is _chirp_edges:
            numerator, denominator = (f0 + f1) * leg, clock_hz * 65536
            a, b = numerator, denominator
            while b:
                a, b = b, a % b
            periods, cycle_count = denominator // a, numerator // a
            edge_count = cycle_count * 2
            # Cache only a genuine repeat of the fixed-point state. In
            # particular, a triangular frequency trajectory is not itself
            # enough: its accumulated carrier phase must repeat as well.
            if (0 < edge_count <= 2048 and periods <= 16 and
                    (2 * periods * leg_phase) & 0xffffffff == 0):
                result._period = 2 * leg * periods
                result._period_edges = edge_count
        return result

    def fill_into(self, buffer, count):
        """Fill preallocated u32 ticks, returning the count or raising on failure."""
        if self._period:
            return self._fill_periodic(buffer, count)
        # Keep native signed clock coordinates small during indefinitely long ONs.
        if self.state[13] >= 0x20000000:
            shift = self.state[13]
            self.origin += shift
            self.state[13] = 0
        count = self.kernel(self.state, buffer, count)
        if count < 0:
            raise ValueError('native edge solver did not converge')
        return count

    def _fill_periodic(self, buffer, count):
        if self._cache is None:
            intro, repeated = words(self._period_edges), words(self._period_edges)
            if self.kernel(self.state, intro, len(intro)) < 0 or self.kernel(self.state, repeated, len(repeated)) < 0:
                raise ValueError('native edge solver did not converge')
            self._cache = intro, repeated
        group = self._cache_position // self._period_edges
        if group * self._period - self.origin >= 0x20000000:
            self.origin = group * self._period
        written = 0
        while written < count:
            group, index = divmod(self._cache_position, self._period_edges)
            size = min(count - written, self._period_edges - index)
            source = self._cache[0] if group == 0 else self._cache[1]
            shift = (0 if group == 0 else (group - 1) * self._period) - self.origin
            _copy_shift(buffer, written, source, index, size, shift)
            self._cache_position += size
            written += size
        return count
