def clkdiv(target_freq, clk_freq=125e6):
    divider = clk_freq / target_freq
    assert 1 <= divider <= 65536
    int_divider = int(divider)
    frac_divider = int((divider - int_divider) * 256)
    return (int_divider, frac_divider)
