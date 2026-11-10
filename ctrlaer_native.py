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

    def _half_view(buffer):
        view = memoryview(buffer)
        return view.cast('B').cast('H') if view.format != 'H' else view

    ptr32 = object
    ptr16 = object
    ptr8 = object
    uint = object

    def viper(function):
        scope = dict(function.__globals__, int=_signed, uint=_unsigned,
                     ptr32=_view, ptr16=_half_view, ptr8=lambda value:memoryview(value).cast("B"))
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
    nodes = int(p[29]); node_step = uint(p[30])
    i = 0
    if int(p[18]) != 0 and n > 0:
        out[0] = uint(time << 4) if nodes else uint(time + dead)
        p[18] = uint(0)
        i = 1
    while i < n:
        wanted = uint(target - acc)
        if nodes:
            previous_error = int(uint(target - node_step) - acc)
            phase_q16 = nodes + (previous_error >> 16)
            dt = ((phase_q16 << 8) + (f >> 9)) // (f >> 8)
        else:
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
        out[i] = (uint((time << 4) + (difference * 16 + (nf >> 1)) // nf)
                  if nodes else uint(time + (dead if high == 0 else 0)))
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
        target = (uint(target + node_step) if nodes else
                  (uint(target + duty) if high == 0 else uint(target - duty)))
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
def _lut_edges(state: ptr32, table: ptr32, out: ptr32, count: int) -> int:
    """Invert Q32 phase using a binary phase grid; only the final bin divides."""
    whole = int(state[0]); fraction = uint(state[1])
    leg_whole = int(state[2]); leg_fraction = uint(state[3]); leg = int(state[4])
    size = int(state[5]); phase_shift = int(state[6])
    duty = uint(state[8]); dead = int(state[9]); kind = int(state[10])
    direction = int(state[11]); base = int(state[13]); last = int(state[14])
    low_mask = uint(state[15]); whole_mask = int(state[16])
    i = 0
    while i < count:
        while whole > leg_whole or (whole == leg_whole and fraction >= leg_fraction):
            borrow = 1 if fraction < leg_fraction else 0
            fraction = uint(fraction - leg_fraction)
            whole -= leg_whole + borrow
            base += leg
            direction = 1 - direction
        if phase_shift < 32:
            index = (whole << (32-phase_shift)) | int(fraction >> phase_shift)
            offset = int(fraction >> (phase_shift-16)) & 65535
        else:
            bits = phase_shift-32
            index = whole >> bits
            offset = ((whole << (16-bits)) | int(fraction >> (16+bits))) & 65535
        if index == last:
            reciprocal = uint(state[7])
            if reciprocal == uint(0):
                offset = 0
            else:
                phase = (uint((whole & whole_mask) << 16) | (fraction >> 16)
                         if phase_shift >= 32 else (fraction & low_mask) >> 16)
                ahi = int(phase >> 16); alo = int(phase & uint(65535))
                rhi = int(reciprocal >> 16); rlo = int(reciprocal & uint(65535))
                low_product = uint(alo * rlo)
                middle = uint(ahi * rlo + int(low_product >> 16))
                middle2 = uint(alo * rhi + int(middle & uint(65535)))
                upper = uint(ahi * rhi + int(middle >> 16) + int(middle2 >> 16))
                lower = uint((middle2 << 16) | (low_product & uint(65535)))
                shift = int(state[12])
                offset = (int(upper >> (shift-32)) if shift >= 32 else
                          int((lower >> shift) | (upper << (32-shift))))
                if offset > 65535:
                    offset = 65535
        row = direction*size+index
        first_time = int(table[row]); delta = int(table[row+1])-first_time
        interpolated = first_time+int((uint(delta*offset)+uint(32768)) >> 16)
        edge = base+((interpolated+8) >> 4)+(dead if kind == 0 else 0)
        out[i] = uint(edge)
        step = duty if kind == 0 else uint(0)-duty
        previous = fraction
        fraction = uint(fraction+step)
        if fraction < previous:
            whole += 1
        kind = 1-kind
        i += 1
    state[0] = uint(whole); state[1] = fraction; state[10] = uint(kind)
    state[11] = uint(direction); state[13] = uint(base)
    return i


@micropython.viper
def _lut_pack(state: ptr32, table: ptr32, output: ptr32, p: ptr32) -> int:
    """Fuse inverse-phase lookup and packets; full blocks skip horizon checks."""
    whole = int(state[0]); fraction = uint(state[1])
    leg_whole = int(state[2]); leg_fraction = uint(state[3]); leg = int(state[4])
    size = int(state[5]); phase_shift = int(state[6])
    duty = uint(state[8]); dead = int(state[9]); kind = int(state[10])
    direction = int(state[11]); base = int(state[13]); last = int(state[14])
    low_mask = uint(state[15]); whole_mask = int(state[16])
    current = int(p[2]); end = int(p[3]); mask = uint(p[4])
    first = uint(p[5]); second = uint(p[6]); position = int(p[8]); limit = int(p[9])
    instruction = uint(p[10]); flexible = int(p[11])
    bound = ((limit-position) >> 1)*int(state[17])+2*int(state[18])
    if end-current > bound and phase_shift >= 18:
        # Keep phase in table cells plus 16 fractional bits. The residual
        # retains all Q32 carrier-phase bits without rebuilding a 64-bit
        # phase/index pair at every edge. The shift bound keeps steps signed.
        drop = phase_shift-16
        residual_mask = uint((1 << drop)-1)
        cell_phase = (whole << (32-drop)) | int(fraction >> drop)
        residual = int(fraction & residual_mask)
        leg_phase = (leg_whole << (32-drop)) | int(leg_fraction >> drop)
        leg_residual = int(leg_fraction & residual_mask)
        rise_high = int(duty >> drop); rise_low = int(duty & residual_mask)
        opposite = uint(uint(0)-duty)
        fall_high = int(opposite >> drop); fall_low = int(opposite & residual_mask)
        aligned = rise_low == 0 and fall_low == 0
        toggle = first ^ second
        while position < limit:
            while cell_phase > leg_phase or (cell_phase == leg_phase and residual >= leg_residual):
                borrow = 1 if residual < leg_residual else 0
                residual = int(uint(residual-leg_residual) & residual_mask)
                cell_phase -= leg_phase+borrow
                base += leg
                direction = 1-direction
            index = cell_phase >> 16
            offset = cell_phase & 65535
            if index == last:
                reciprocal = uint(state[7])
                if reciprocal == uint(0):
                    offset = 0
                else:
                    fraction = uint(cell_phase << drop) | uint(residual)
                    whole = cell_phase >> (32-drop)
                    tail = (uint((whole & whole_mask) << 16) | (fraction >> 16)
                            if phase_shift >= 32 else (fraction & low_mask) >> 16)
                    ahi = int(tail >> 16); alo = int(tail & uint(65535))
                    rhi = int(reciprocal >> 16); rlo = int(reciprocal & uint(65535))
                    low_product = uint(alo * rlo)
                    middle = uint(ahi * rlo + int(low_product >> 16))
                    middle2 = uint(alo * rhi + int(middle & uint(65535)))
                    upper = uint(ahi * rhi + int(middle >> 16) + int(middle2 >> 16))
                    lower = uint((middle2 << 16) | (low_product & uint(65535)))
                    shift = int(state[12])
                    offset = (int(upper >> (shift-32)) if shift >= 32 else
                              int((lower >> shift) | (upper << (32-shift))))
                    if offset > 65535:
                        offset = 65535
            row = direction*size+index
            first_time = int(table[row]); delta = int(table[row+1])-first_time
            interpolated = first_time+int((uint(delta*offset)+uint(32768)) >> 16)
            edge = base+((interpolated+8) >> 4)+(dead if kind == 0 else 0)
            if edge > current:
                length = edge-current
                if length < 6:
                    return -1
                output[position] = instruction | mask << 16
                output[position+1] = uint(length-6)
                position += 2
                current = edge
            mask ^= toggle
            if aligned:
                cell_phase += rise_high if kind == 0 else fall_high
            else:
                residual += rise_low if kind == 0 else fall_low
                cell_phase += (rise_high if kind == 0 else fall_high)+(residual >> drop)
                residual &= int(residual_mask)
            kind = 1-kind
        whole = cell_phase >> (32-drop)
        fraction = uint(cell_phase << drop) | uint(residual)
    else:
        while position < limit and current < end:
            while whole > leg_whole or (whole == leg_whole and fraction >= leg_fraction):
                borrow = 1 if fraction < leg_fraction else 0
                fraction = uint(fraction - leg_fraction)
                whole -= leg_whole + borrow
                base += leg
                direction = 1 - direction
            if phase_shift < 32:
                index = (whole << (32-phase_shift)) | int(fraction >> phase_shift)
                offset = int(fraction >> (phase_shift-16)) & 65535
            else:
                bits = phase_shift-32
                index = whole >> bits
                offset = ((whole << (16-bits)) | int(fraction >> (16+bits))) & 65535
            if index == last:
                reciprocal = uint(state[7])
                if reciprocal == uint(0):
                    offset = 0
                else:
                    phase = (uint((whole & whole_mask) << 16) | (fraction >> 16)
                             if phase_shift >= 32 else (fraction & low_mask) >> 16)
                    ahi = int(phase >> 16); alo = int(phase & uint(65535))
                    rhi = int(reciprocal >> 16); rlo = int(reciprocal & uint(65535))
                    low_product = uint(alo * rlo)
                    middle = uint(ahi * rlo + int(low_product >> 16))
                    middle2 = uint(alo * rhi + int(middle & uint(65535)))
                    upper = uint(ahi * rhi + int(middle >> 16) + int(middle2 >> 16))
                    lower = uint((middle2 << 16) | (low_product & uint(65535)))
                    shift = int(state[12])
                    offset = (int(upper >> (shift-32)) if shift >= 32 else
                              int((lower >> shift) | (upper << (32-shift))))
                    if offset > 65535:
                        offset = 65535
            row = direction*size+index
            first_time = int(table[row]); delta = int(table[row+1])-first_time
            interpolated = first_time+int((uint(delta*offset)+uint(32768)) >> 16)
            edge = base+((interpolated+8) >> 4)+(dead if kind == 0 else 0)
            if edge <= current:
                mask = first if kind == 0 else second
                step = duty if kind == 0 else uint(0)-duty
                previous = fraction
                fraction = uint(fraction+step)
                if fraction < previous:
                    whole += 1
                kind = 1-kind
                continue
            if flexible and edge > end and edge-end < 6:
                end = edge
            stop = edge if edge < end else end
            length = stop-current
            if length < 6:
                return -1
            output[position] = instruction | mask << 16
            output[position+1] = uint(length-6)
            position += 2
            current = stop
            if position < limit and current < end and current == edge:
                mask = first if kind == 0 else second
                step = duty if kind == 0 else uint(0)-duty
                previous = fraction
                fraction = uint(fraction+step)
                if fraction < previous:
                    whole += 1
                kind = 1-kind
    state[0] = uint(whole); state[1] = fraction; state[10] = uint(kind)
    state[11] = uint(direction); state[13] = uint(base)
    p[2] = uint(current); p[4] = mask; p[8] = uint(position)
    return position


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
def _record_intervals(source: ptr32, count: int, destination: ptr16, p: ptr32) -> int:
    position = int(p[0]); previous = int(p[1]); first = int(p[2]); i = 0
    while i < count:
        tick = int(source[i])
        if position == 0:
            first = tick
        else:
            gap = tick - previous
            if gap <= 0 or gap > 65535:
                return -1
            destination[position - 1] = uint(gap)
        position += 1
        previous = tick
        i += 1
    p[0] = uint(position); p[1] = uint(previous); p[2] = uint(first)
    return position


@micropython.viper
def _copy_intervals(destination: ptr32, dest_start: int, source: ptr16,
                    source_start: int, count: int, time: int, step: int) -> int:
    i = 0
    while i < count:
        destination[dest_start + i] = uint(time)
        time += int(source[source_start])
        source_start += step
        i += 1
    return time


@micropython.viper
def _record_intervals8(source: ptr32, count: int, destination: ptr8, p: ptr32) -> int:
    position = int(p[0]); previous = int(p[1]); first = int(p[2]); base = int(p[3]); i = 0
    while i < count:
        tick = int(source[i])
        if position == 0:
            first = tick
        else:
            gap = tick - previous
            if gap < base or gap-base > 255:
                return -1
            destination[position - 1] = uint(gap-base)
        position += 1
        previous = tick
        i += 1
    p[0] = uint(position); p[1] = uint(previous); p[2] = uint(first)
    return position


@micropython.viper
def _copy_intervals8(destination: ptr32, dest_start: int, source: ptr8,
                    source_start: int, count: int, time: int, base: int, step: int) -> int:
    i = 0
    while i < count:
        destination[dest_start + i] = uint(time)
        time += int(source[source_start])+base
        source_start += step
        i += 1
    return time


@micropython.viper
def _stream_pack(edges: ptr32, output: ptr32, p: ptr32) -> int:
    """Continue one carrier into an existing packet block, without allocation.

    p: edge index/count, current/end ticks, mask, first/second masks, parity,
    output index/limit, OUT instruction, flexible end. Coordinates are local to an edge batch.
    An edge at the right boundary is retained for the next call.
    """
    index = int(p[0]); count = int(p[1]); current = int(p[2]); end = int(p[3])
    mask = uint(p[4]); first = uint(p[5]); second = uint(p[6]); parity = int(p[7])
    position = int(p[8]); limit = int(p[9]); instruction = uint(p[10])
    flexible = int(p[11])
    while index < count and int(edges[index]) <= current:
        mask = first if parity == 0 else second
        parity = 1 - parity
        index += 1
    available = count - index
    if available > (limit - position) >> 1:
        available = (limit - position) >> 1
    if available > 0 and int(edges[index + available - 1]) <= end:
        # The usual full batch has no horizon/command end inside it. Keep
        # boundary checks out of this loop, and leave its final edge pending.
        stop_index = index + available
        last_mask = mask
        while index < stop_index:
            edge = int(edges[index])
            length = edge - current
            if length < 6:
                return -1
            output[position] = instruction | mask << 16
            output[position + 1] = uint(length - 6)
            position += 2
            current = edge
            last_mask = mask
            mask = first if parity == 0 else second
            parity = 1 - parity
            index += 1
        p[0] = uint(index - 1); p[2] = uint(current); p[4] = last_mask
        p[7] = uint(1 - parity); p[8] = uint(position)
        return position
    while position < limit and current < end and index < count:
        edge = int(edges[index])
        if edge <= current:
            mask = first if parity == 0 else second
            parity = 1 - parity
            index += 1
            continue
        if flexible and edge > end and edge - end < 6:
            end = edge
        stop = edge if edge < end else end
        length = stop - current
        if length < 6:
            return -1
        output[position] = instruction | mask << 16
        output[position + 1] = uint(length - 6)
        position += 2
        current = stop
    p[0] = uint(index); p[2] = uint(current); p[4] = mask
    p[7] = uint(parity); p[8] = uint(position)
    return position


@micropython.viper
def _pulse_pack(state: ptr32, output: ptr32, p: ptr32) -> int:
    """Fuse the constant-frequency edge accumulator and packet writer."""
    time = int(state[13]); fraction = uint(state[24]); kind = int(state[12])
    high_ticks = int(state[20]); high_fraction = uint(state[21])
    low_ticks = int(state[22]); low_fraction = uint(state[23]); dead = int(state[19])
    current = int(p[2]); end = int(p[3]); mask = uint(p[4])
    first = uint(p[5]); second = uint(p[6]); position = int(p[8]); limit = int(p[9])
    instruction = uint(p[10]); flexible = int(p[11])
    edge = time + int(fraction >> 31) + (dead if kind == 0 else 0)
    while edge <= current:
        previous = fraction
        if kind == 0:
            mask = first
            time += high_ticks
            fraction = uint(fraction + high_fraction)
        else:
            mask = second
            time += low_ticks
            fraction = uint(fraction + low_fraction)
        if fraction < previous:
            time += 1
        kind = 1 - kind
        edge = time + int(fraction >> 31) + (dead if kind == 0 else 0)
    while position < limit and current < end:
        if flexible and edge > end and edge - end < 6:
            end = edge
        stop = edge if edge < end else end
        length = stop - current
        if length < 6:
            return -1
        output[position] = instruction | mask << 16
        output[position + 1] = uint(length - 6)
        position += 2
        current = stop
        if position >= limit or current >= end:
            break
        previous = fraction
        if kind == 0:
            mask = first
            time += high_ticks
            fraction = uint(fraction + high_fraction)
        else:
            mask = second
            time += low_ticks
            fraction = uint(fraction + low_fraction)
        if fraction < previous:
            time += 1
        kind = 1 - kind
        edge = time + int(fraction >> 31) + (dead if kind == 0 else 0)
    state[12] = uint(kind); state[13] = uint(time); state[24] = fraction
    p[2] = uint(current); p[4] = mask; p[8] = uint(position)
    return position


@micropython.viper
def _cached_pack(state: ptr32, table: ptr8, output: ptr32, p: ptr32) -> int:
    """Pack exact cached intervals directly, including a mirrored return leg."""
    edge = int(state[13]); index = int(state[0]); full = int(state[1])
    half = int(state[2]); base = int(state[3]); kind = int(state[10])
    bytes_table = ptr8(table)
    shorts = ptr16(table) if base == 0 else ptr16(output)
    current = int(p[2]); end = int(p[3]); mask = uint(p[4])
    first = uint(p[5]); second = uint(p[6]); position = int(p[8]); limit = int(p[9])
    instruction = uint(p[10]); flexible = int(p[11])
    while edge <= current:
        row = index if index < half else full-1-index
        edge += int(bytes_table[row])+base if base else int(shorts[row])
        index += 1
        if index == full:
            index = 0
        mask = first if kind == 0 else second
        kind = 1-kind
    while position < limit and current < end:
        if flexible and edge > end and edge - end < 6:
            end = edge
        stop = edge if edge < end else end
        length = stop - current
        if length < 6:
            return -1
        output[position] = instruction | mask << 16
        output[position + 1] = uint(length - 6)
        position += 2
        current = stop
        if position >= limit or current >= end:
            break
        row = index if index < half else full-1-index
        edge += int(bytes_table[row])+base if base else int(shorts[row])
        index += 1
        if index == full:
            index = 0
        mask = first if kind == 0 else second
        kind = 1-kind
    state[0] = uint(index); state[10] = uint(kind); state[13] = uint(edge)
    p[2] = uint(current); p[4] = mask; p[8] = uint(position)
    return position


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
        self._cache_base = 0
        self._fold_cache = False
        self._packed_cache = False
        self._cache_position = 0
        self._cache_tick = 0
        self._lut = None

    @classmethod
    def create(cls, command, clock_hz, offset_seconds=0, _lut=None):
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
        state = [0] * 31
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
            slope_error = (abs(slope * clock_hz * leg - ((f1-f0) << 40)) /
                           (clock_hz * leg) * leg * leg * clock_hz /
                           (144115188075855872 * min(frequency,end)))
            numerator, denominator = (f0 + f1) * leg, clock_hz * 65536
            a, b = numerator, denominator
            while b:
                a, b = b, a % b
            periods, cycle_count = denominator // a, numerator // a
            edge_count = cycle_count * 2
            # Cache only a genuine repeat of the fixed-point state. In
            # particular, a triangular frequency trajectory is not itself
            # enough: its accumulated carrier phase must repeat as well.
            maximum_gap = clock_hz / min(frequency, end) * max(duty, 1-duty) + dead + 2
            if (0 < edge_count <= 10000 and periods <= 16 and maximum_gap <= 65535
                    and slope_error <= .4 and
                    (2 * periods * leg_phase) & 0xffffffff == 0):
                result._period = 2 * leg * periods
                result._period_edges = edge_count
                # Half-cycle-aligned phase and a 50% carrier make the down
                # traversal the exact time reversal of the up traversal.
                result._fold_cache = (periods == 1 and first_phase == 0 and
                    high_phase == 0x80000000 and dead == 0 and
                    leg_phase & 0x7fffffff == 0)
                minimum_gap = int(clock_hz/max(frequency,end)*min(duty,1-duty))-dead-2
                if minimum_gap > 0 and maximum_gap-minimum_gap <= 255:
                    result._cache_base = minimum_gap
            if not result._period:
                result._enable_lut(command, first_phase, leg_phase, slope, slope_error, _lut)
                if result._lut is None and slope_error > .4:
                    return None
        return result

    def _enable_lut(self, command, first_phase, leg_phase, slope, slope_error, reuse):
        """Invert phase on a binary grid, retaining a bounded Q4-time table."""
        leg = self.state[4]
        low = min(command.freq, command.end_freq)
        cycles = leg_phase / 4294967296
        if low < 10000 or leg >= 0x08000000 or cycles >= 32768:
            return
        if reuse is None:
            seconds = leg / self.clock_hz
            error_scale = (self.clock_hz * abs(command.end_freq-command.freq) *
                           cycles * cycles / (8 * seconds * low ** 3))
            gap_scale = 16 * self.clock_hz * cycles / low
            bins = 32
            while error_scale/(bins*bins) > .08 or gap_scale/bins+32 > 65535:
                bins *= 2
                if bins > 2048:
                    return
            phase_step, phase_shift = 65536, 16
            while phase_step*2*bins <= leg_phase:
                phase_step *= 2
                phase_shift += 1
            full = leg_phase//phase_step
            size = full+2
            if size > 4098:
                return
            remainder = leg_phase-full*phase_step
            reciprocal = shift = 0
            if remainder >= 65536:
                power = 60
                reciprocal = (1 << (power+16))//remainder
                while reciprocal > 0xffffffff:
                    power -= 1
                    reciprocal = (1 << (power+16))//remainder
                shift = power-16
            owner = bytearray(size*8)
            try:
                from uctypes import struct, addressof, ARRAY, UINT32
            except ImportError:
                table = memoryview(owner).cast('I')
                raw, split = table, size
            else:
                table = struct(addressof(owner), {'words':(ARRAY, UINT32 | size*2)}).words
                raw, split = memoryview(owner), size*4
            frequencies = (round(command.freq*65536),round(command.end_freq*65536))
            starts = ((self.state[5],self.state[16],self.state[27]),
                      (self.state[6],self.state[17],self.state[28]))
            nodes = array('I',self.state)
            for direction in (0,1):
                nodes[0],nodes[1],nodes[26] = starts[direction]
                nodes[5],nodes[16],nodes[27] = starts[direction]
                nodes[6],nodes[17],nodes[28] = starts[1-direction]
                signed = slope if direction == 0 else -slope
                nodes[2],nodes[25] = (signed >> 8) & 0xffffffff,signed & 255
                nodes[3],nodes[7],nodes[8],nodes[9] = leg,leg_phase & 0xffffffff,leg_phase & 0xffffffff,0
                nodes[10],nodes[12],nodes[13],nodes[14] = phase_step & 0xffffffff,1,0,0
                nodes[18],nodes[19],nodes[29],nodes[30] = 1,0,phase_step >> 16,phase_step & 0xffffffff
                target = raw[:split] if direction == 0 else raw[split:]
                if _chirp_edges(nodes,target,full+1) < 0:
                    raise ValueError('native inverse-phase table solver did not converge')
                table[direction*size] = 0
                table[(direction+1)*size-1] = leg << 4
                if not remainder:
                    table[direction*size+full] = leg << 4
                # Refine long shallow trajectories when slope rounding would
                # otherwise consume the one-clock interpolation error budget.
                ratio = phase_step/leg_phase
                curvature = error_scale*ratio*ratio
                gap = gap_scale*ratio
                allowance = .95-(.5+1/16+gap/(16*65536)+
                                  self.clock_hz/(low*65536)+curvature+.004)
                if slope_error > allowance:
                    start = frequencies[direction]
                    delta = frequencies[1-direction]-start
                    linear = start*(leg << 12)
                    for index in range(1,full+1):
                        target_phase = index*phase_step*self.clock_hz*leg
                        position = direction*size+index
                        tick = table[position]
                        for _ in range(3):
                            difference = target_phase-linear*tick-128*delta*tick*tick
                            derivative = linear+256*delta*tick
                            change = _ratio(difference,derivative)
                            if not change:
                                break
                            tick += change
                        table[position] = tick
            reuse = owner,table,size,phase_shift,reciprocal,shift
        owner,table,size,phase_shift,reciprocal,shift = reuse
        part,phase = divmod(first_phase,leg_phase)
        low_mask = (1 << phase_shift)-1 if phase_shift < 32 else 0xffffffff
        whole_mask = (1 << (phase_shift-32))-1 if phase_shift >= 32 else 0
        self.state = array('I',[phase >> 32,phase & 0xffffffff,
            leg_phase >> 32,leg_phase & 0xffffffff,leg,size,phase_shift,reciprocal,
            self.state[11],self.state[19],0,part & 1,shift,part*leg,size-2,
            low_mask,whole_mask,
            int(self.clock_hz*max(command.duty,1-command.duty)/low)+self.state[19]+3,
            int(self.clock_hz/low)+2]+[0]*12)
        self.kernel,self._lut = _lut_edges,reuse

    def fill_into(self, buffer, count):
        """Fill preallocated u32 ticks, returning the count or raising on failure."""
        if count == 0:
            return 0
        if self._period:
            return self._fill_periodic(buffer, count)
        # Keep native signed clock coordinates small during indefinitely long ONs.
        if self.state[13] >= 0x20000000:
            shift = self.state[13]
            self.origin += shift
            self.state[13] = 0
        count = (self.kernel(self.state, self._lut[1], buffer, count) if self._lut is not None
                 else self.kernel(self.state, buffer, count))
        if count < 0:
            raise ValueError('native edge solver did not converge')
        return count

    def _fill_periodic(self, buffer, count):
        if self._cache is None:
            base = self._cache_base
            folded = self._fold_cache
            cache_count = self._period_edges//2 if folded else self._period_edges
            owner = bytearray(cache_count * (1 if base else 2))
            try:
                from uctypes import struct, addressof, ARRAY, UINT8, UINT16
            except ImportError:
                table = memoryview(owner) if base else memoryview(owner).cast('H')
            else:
                table = struct(addressof(owner),
                               {'words':(ARRAY, (UINT8 if base else UINT16) | cache_count)}).words
            record = words(4)
            record[3] = base
            while record[0] < cache_count:
                part = min(count, cache_count-record[0])
                if self.kernel(self.state, buffer, part) < 0:
                    raise ValueError('native edge solver did not converge')
                written = (_record_intervals8(buffer,part,table,record) if base else
                           _record_intervals(buffer,part,table,record))
                if written < 0:
                    raise ValueError('cached edge interval does not fit its encoding')
            span = self._period//2 if folded else self._period
            wrap = span+record[2]-record[1]
            if not 0 < wrap <= 65535:
                raise ValueError('cached period boundary does not fit u16')
            if base and not 0 <= wrap-base <= 255:
                raise ValueError('cached period boundary does not fit u8 delta')
            table[cache_count-1] = wrap-base
            # Both ideal frequency and integrated carrier phase repeat after
            # this exact integer-clock period. Reuse the first period itself:
            # its quantisation error repeats without accumulating. Retain the
            # owner beside the raw uctypes view, including across cursor reuse.
            self._cache = owner, table, record[2], base, folded
        group, index = divmod(self._cache_position, self._period_edges)
        if index == 0:
            self._cache_tick = group * self._period + self._cache[2]
        if group * self._period - self.origin >= 0x20000000:
            self.origin = group * self._period
        written = 0
        while written < count:
            group, index = divmod(self._cache_position, self._period_edges)
            size = min(count-written,self._period_edges-index)
            source,base,folded = self._cache[1],self._cache[3],self._cache[4]
            step = 1
            if folded:
                half = self._period_edges//2
                if index < half:
                    size = min(size,half-index)
                else:
                    index = self._period_edges-1-index
                    step = -1
            next_tick = (_copy_intervals8(buffer,written,source,index,size,
                                         self._cache_tick-self.origin,base,step) if base else
                         _copy_intervals(buffer,written,source,index,size,
                                         self._cache_tick-self.origin,step))
            self._cache_tick = next_tick + self.origin
            self._cache_position += size
            written += size
        return count
