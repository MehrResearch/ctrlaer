"""Small, labelled SVG drawings for the documentation (no hardware access)."""
from html import escape
from pathlib import Path

INK, BLUE, GREEN, GREY = '#243247', '#355cbb', '#137b67', '#64748b'

class Drawing:
    def __init__(self, width, height, title):
        self.width, self.height = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
                      '<title>'+escape(title)+'</title>',
                      '<rect width="100%" height="100%" rx="10" fill="#fff"/>',
                      '<g font-family="system-ui, sans-serif" fill="'+INK+'">']
        self.text(24, 32, title, 18, weight='600')
    def text(self, x, y, value, size=14, color=INK, weight='400', anchor='start'):
        size *= 1.3
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')
    def line(self, x1, y1, x2, y2, color=BLUE, width=2, dash=False):
        self.parts.append(f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="{color}" stroke-width="{width}"'+(' stroke-dasharray="5 5"' if dash else '')+'/>')
    def box(self, x, y, width, height, color='#f1f5f9', stroke='#cad3df'):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="{color}" stroke="{stroke}"/>')
    def finish(self):
        return '\n'.join(self.parts+['</g></svg>'])+'\n'

def waveform_svg(program, window_ms=None, **unused):
    duration = program.duration_ticks
    if window_ms is not None:
        duration = min(duration, round(window_ms * program.clock_hz / 1000))
    d = Drawing(1000, 110+88*program.n_pins, 'Expected output')
    left, right = 132, 970
    scale = (right-left)/max(duration,1)
    milliseconds = duration/program.clock_hz*1000
    use_us = milliseconds <= 0.2
    for index in range(6):
        x = left+(right-left)*index/5
        d.line(x, 54, x, 58+88*program.n_pins, '#e5eaf1', 1)
        d.text(x, 78+88*program.n_pins, f'{milliseconds*index/5*(1000 if use_us else 1):.4g}', 12, GREY, anchor='middle')
    d.text((left+right)/2, 101+88*program.n_pins, 'Time (µs)' if use_us else 'Time (ms)', 13, anchor='middle')
    for channel in range(program.n_pins):
        high, low = 63+88*channel, 99+88*channel
        d.text(20, high+20, program.names[channel], 14, weight='600')
        d.text(left-12, high+4, 'HIGH', 10, GREY, anchor='end')
        d.text(left-12, low+4, 'LOW', 10, GREY, anchor='end')
        d.line(left, high, right, high, '#e5eaf1', 1)
        d.line(left, low, right, low, '#e5eaf1', 1)
        path = f'M {left},{low}'
        for tick, level in program.edges(channel):
            if tick > duration:
                break
            path += f' H {left+tick*scale:.3f} V {high if level else low}'
        path += f' H {right}'
        d.parts.append(f'<path d="{path}" stroke="{BLUE if channel%2==0 else GREEN}" stroke-width="2" fill="none"/>')
    for gate in program.gates:
        tick = gate['nominal_tick']
        if tick <= duration:
            x = left+tick*scale
            d.line(x, 48, x, 58+88*program.n_pins, '#a75b12', 1.5, True)
            d.text(x+6, 48, 'Wait for input', 11, '#a75b12')
    return d.finish()

def device(d, x, y, title, rows, width=245):
    d.box(x, y, width, 65+len(rows)*42)
    d.text(x+18, y+29, title, 17, weight='600')
    ports=[]
    for index, row in enumerate(rows):
        yy=y+65+index*42
        d.text(x+18, yy+5, row, 13)
        ports.append(yy)
    return ports

def build_diagrams(destination):
    destination=Path(destination)
    def save(filename,d):
        (destination/filename).write_text(d.finish())

    d=Drawing(900,700,'Raspberry Pi Pico / Pico 2 · top view')
    d.box(330,62,240,600,'#e8f4ef','#137b67')
    d.box(398,45,104,50,'#e2e8f0')
    d.text(450,77,'USB',14,anchor='middle')
    left=['GP0','GP1','GND','GP2','GP3','GP4','GP5','GND','GP6','GP7','GP8','GP9','GND','GP10','GP11','GP12','GP13','GND','GP14','GP15']
    right=['VBUS','VSYS','GND','3V3_EN','3V3(OUT)','ADC_VREF','GP28','GND','GP27','GP26','RUN','GP22','GND','GP21','GP20','GP19','GP18','GND','GP17','GP16']
    selected={'GP0','GP1','GP6','GP7','GP16','GP26','GND','3V3(OUT)'}
    for index,(a,b) in enumerate(zip(left,right)):
        yy=117+index*27
        for xx, value, number, label_x, anchor in ((330,a,index+1,304,'end'),(570,b,40-index,596,'start')):
            color=BLUE if value in selected else GREY
            d.line(xx-7,yy,xx+7,yy,color,6)
            d.text(label_x,yy+5,f'{number:2d}  {value}',14,color,weight='600' if value in selected else '400',anchor=anchor)
    d.text(450,685,'GP numbers are GPIO names; 1–40 are physical positions.',14,anchor='middle')
    save('pico-pins.svg',d)

    d=Drawing(900,255,'LED test · GP0 → 1 kΩ → LED → GND')
    ys=device(d,28,67,'Pico',['GP0 · pin 1','GND · pin 3'])
    d.line(273,ys[0],385,ys[0]); d.box(385,ys[0]-13,94,26,'#fff')
    d.text(432,ys[0]-23,'1 kΩ',14,anchor='middle');d.line(479,ys[0],585,ys[0])
    d.parts.append(f'<circle cx="615" cy="{ys[0]}" r="25" fill="#fee2e2" stroke="#c74444"/>')
    d.text(615,ys[0]+5,'LED',12,anchor='middle')
    d.text(577,ys[0]+44,'Anode (+)',12);d.text(688,ys[0]+44,'Cathode (−)',12)
    d.line(640,ys[0],810,ys[0]);d.line(810,ys[0],810,ys[1]);d.line(273,ys[1],810,ys[1],GREY)
    d.text(28,238,'The longer LED leg is usually the anode; the flat side marks the cathode.',13,GREY)
    save('pico-led.svg',d)

    for filename,title,left_title,left_rows,right_title,right_rows,signal in [
        ('pico-driver.svg','Control a driver or instrument','Pico',['GP0 · pin 1','GND · pin 3'],'Driver / instrument',['3.3 V control input','Logic GND'],'Signal →'),
        ('pico-trigger.svg','Receive an external trigger','Instrument',['3.3 V trigger output','Signal GND'],'Pico',['GP6 input · pin 9','GND · pin 8'],'Trigger →'),
        ('pico-probe.svg','Use a second Pico to measure the output','Sending Pico',['GP0 output · pin 1','GND · pin 3'],'Receiving Pico',['GP0 input · pin 1','GND · pin 3'],'Signal →')]:
        d=Drawing(900,275,title)
        a=device(d,28,72,left_title,left_rows);b=device(d,627,72,right_title,right_rows)
        for index,(yy1,yy2) in enumerate(zip(a,b)):
            d.line(273,yy1,627,yy2,BLUE if index==0 else GREY)
            d.text(450,yy1-12,signal if index==0 else 'Common ground',14,anchor='middle')
        if filename=='pico-driver.svg':
            d.text(28,252,'The driver supplies power to the light, valve, motor or transducer.',13,GREY)
        elif filename=='pico-probe.svg':
            d.text(28,252,'Power each Pico from USB. Only the sender drives GP0.',13,GREY)
        save(filename,d)

    d=Drawing(900,265,'Pushbutton trigger · press to connect GP6 to 3.3 V')
    a=device(d,28,72,'Pico',['3V3(OUT) · pin 36','GP6 input · pin 9'])
    d.line(273,a[0],700,a[0]);d.line(700,a[0],700,a[1]-10)
    d.line(700,a[1]-10,735,a[1]-33);d.line(700,a[1],700,a[1]+10)
    d.line(273,a[1],700,a[1]);d.text(746,a[1]-5,'Button',14)
    d.text(28,242,'Enable Pin.PULL_DOWN in code so GP6 reads LOW when the button is open.',13,GREY)
    save('pico-button.svg',d)

    d=Drawing(1000,210,'From an experiment to its output')
    for x,title,subtitle in [(24,'Experiment function','Loops, durations and waits'),(362,'PIO state machine','Produces the electrical signal'),(700,'Driver or instrument','Controls the physical device')]:
        d.box(x,72,276,92)
        d.text(x+138,106,title,17,weight='600',anchor='middle');d.text(x+138,139,subtitle,12,anchor='middle')
    for x in (300,638):
        d.text(x+31,126,'→',28,BLUE,anchor='middle')
    save('how-it-works.svg',d)

    d=Drawing(1000,205,'Illuminate → switch off → read the sensor')
    entries=[(24,270,'Light HIGH','20 ms',BLUE),(306,180,'Light LOW','1 ms',GREEN),(498,478,'Read sensor / choose next action','Duration depends on Python',GREY)]
    for x,width,title,subtitle,color in entries:
        d.box(x,75,width,90)
        d.text(x+width/2,111,title,16,color,weight='600',anchor='middle');d.text(x+width/2,143,subtitle,13,anchor='middle')
    save('eager-order.svg',d)
