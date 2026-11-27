"""Score-bar GIFs (four / six / wicket / no ball / fifty / century). HOW TO REMAKE (run inside score_bars/):
  WORD=FOUR   OUT=four   ACC=255,225,80 BARD=8,28,105 BARM=24,84,205 EDGE=130,200,255 STREAK=140,220,255 python3 make_bars.py full
  WORD=SIX    OUT=six    ACC=20,30,100  BARD=105,45,0 BARM=212,128,8 EDGE=255,215,110 STREAK=255,245,180 python3 make_bars.py full
  WORD=WICKET OUT=wicket ACC=255,225,80 BARD=95,0,12  BARM=195,20,36 EDGE=255,120,120 STREAK=255,170,90  python3 make_bars.py full
  WORD="NO BALL" OUT=noball ACC=255,225,80 BARD=48,10,100 BARM=125,40,205 EDGE=200,150,255 STREAK=230,180,255 python3 make_bars.py full
  BARH=110 WORD=FIFTY   OUT=fifty   ACC=255,225,80 BARD=0,55,70  BARM=0,135,150  EDGE=120,240,240 STREAK=170,255,250 python3 make_bars.py full
  BARH=110 WORD=CENTURY OUT=century ACC=255,215,90 BARD=85,0,55  BARM=185,20,110 EDGE=255,140,210 STREAK=255,200,120 python3 make_bars.py full
"""
import math, sys, subprocess
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops
F=str(__import__('pathlib').Path(__file__).resolve().parent.parent)+'/'
S=2                                   # supersample
BARH=int(__import__('os').environ.get('BARH','70'))   # bar thickness (70 = four/six/wicket, 110 = milestones)
W,H=1000,BARH+80                      # output size
BX0,BX1,BY0,BY1=0,1000,40,40+BARH      # the bar: full width
FPS=20; DUR=3.4; N=int(FPS*DUR)
import os
WORD=os.environ.get('WORD','WICKET')
CYAN=tuple(int(x) for x in os.environ.get('ACC','80,240,255').split(','))
WHITE=(245,248,255); PINK=tuple(int(x) for x in os.environ.get('STREAK','255,90,220').split(','))
OUT=os.environ.get('OUT','wicket_bar')
BAR_DARK=tuple(int(x) for x in os.environ.get('BARD','40,12,90').split(','))
BAR_MID=tuple(int(x) for x in os.environ.get('BARM','90,22,160').split(','))
EDGE=tuple(int(x) for x in os.environ.get('EDGE','160,120,255').split(','))
font=lambda sz: ImageFont.truetype(F+'label.ttf', int(sz*S*min(1.45,BARH/70)))
def ease(t): t=max(0,min(1,t)); return 1-(1-t)**3
def prog(t,a,b): return max(0,min(1,(t-a)/(b-a)))

def bar_base():
    w,h=(BX1-BX0)*S,(BY1-BY0)*S
    g=Image.new('RGB',(w,h))
    px=g.load()
    for x in range(w):
        u=x/w
        # deep indigo -> violet -> indigo, like a broadcast bar
        k=math.sin(u*math.pi)
        c=tuple(int(BAR_DARK[i]+(BAR_MID[i]-BAR_DARK[i])*k) for i in range(3))
        for y in range(h): px[x,y]=c
    d=ImageDraw.Draw(g,'RGBA')
    d.rectangle([0,0,w,int(h*0.45)],fill=(255,255,255,18))            # gloss
    d.line([(0,0),(w,0)],fill=EDGE+(200,),width=2*S); d.line([(0,h-1),(w,h-1)],fill=(20,0,60,255),width=2*S)
    return g
BAR=bar_base()
BW,BH=BAR.size

def text_img(txt,sz,fill,spacing=0):
    f=font(sz); ws=[f.getlength(c) for c in txt]; tw=int(sum(ws)+spacing*S*(len(txt)-1))+4*S
    im=Image.new('RGBA',(tw,int(sz*1.4*S)),(0,0,0,0)); d=ImageDraw.Draw(im); x=2*S
    for c,w_ in zip(txt,ws):
        d.text((x+2*S,im.height/2+3*S),c,font=f,fill=(0,0,0,150),anchor='lm')
        d.text((x,im.height/2),c,font=f,fill=fill,anchor='lm',stroke_width=S,stroke_fill=(0,0,0,90)); x+=w_+spacing*S
    return im

def glow(im,col,r=6):
    a=im.getchannel('A').filter(ImageFilter.GaussianBlur(r*S))
    g=Image.new('RGBA',im.size,(0,0,0,0)); g.putalpha(a.point(lambda v:int(v*0.55)))
    out=Image.new('RGBA',im.size,(0,0,0,0)); out.alpha_composite(g); out.alpha_composite(im); return out

def mblur(im,amount):
    if amount<1: return im
    k=int(amount)
    acc=Image.new('RGBA',im.size,(0,0,0,0))
    n=6
    for i in range(n):
        sh=Image.new('RGBA',im.size,(0,0,0,0)); sh.alpha_composite(im,(int(-k*i/n),0))
        acc=Image.blend(acc,sh,1/(i+1))
    return acc

SPC=int(14*6/len(WORD)) if len(WORD)<6 else (14 if len(WORD)==6 else 4)
POPSZ=int(40*min(1.35,6/len(WORD)))
WK_BIG_C=glow(text_img(WORD,46,CYAN,SPC),CYAN,5)
WK_BIG_W=glow(text_img(WORD,46,WHITE,SPC),(150,180,255),4)
WK_POP=glow(text_img(WORD,POPSZ,CYAN,4 if len(WORD)<6 else 0),CYAN,5)
WK_TRI_C=glow(text_img(WORD,POPSZ,CYAN),CYAN,4); WK_TRI_W=text_img(WORD,POPSZ,WHITE)
WK_MID_C=glow(text_img(WORD,40,CYAN),CYAN,4)
WK_MID_W=text_img(WORD,40,WHITE)
WK_SM_C=text_img(WORD,30,CYAN); WK_SM_W=text_img(WORD,30,WHITE)

def frame(i):
    t=i/FPS
    canvas=Image.new('RGBA',(W*S,H*S),(0,0,0,0))
    cd=ImageDraw.Draw(canvas,'RGBA')
    # soft glow behind the bar
    open_=ease(prog(t,0.0,0.35)); close=ease(prog(t,DUR-0.3,DUR))
    vis=open_*(1-close)
    if vis<=0: return canvas.resize((W,H),Image.LANCZOS)
    bar=BAR.copy().convert('RGBA'); bd=ImageDraw.Draw(bar,'RGBA')
    # ---- content inside the bar ----
    cy=BH//2
    def put(im,cx):
        bar.alpha_composite(im,(int(cx-im.width/2),int(cy-im.height/2)))
    if t<0.85:                                   # 1) three wide WICKETs slide in from the right
        p=ease(prog(t,0.25,0.75)); off=(1-p)*BW*0.9
        for k,(im) in enumerate((WK_BIG_C,WK_BIG_W,WK_BIG_C)):
            cx=BW*(0.17+0.33*k)+off
            put(mblur(im,(1-p)*90*S) if p<1 else im,cx)
    elif t<1.1:                                  # 2) glitch: only slices of the letters
        f=int((t-0.85)*FPS)
        for k in range(5):
            cx=BW*(0.1+0.2*k)
            im=WK_MID_C if (k+f)%2==0 else WK_MID_W
            band=Image.new('L',im.size,0); bdd=ImageDraw.Draw(band)
            x0=int(im.width*(0.45+0.25*((f+k)%3)/2)); bdd.rectangle([x0,0,im.width,im.height],fill=255)
            sl=im.copy(); sl.putalpha(ImageChops.multiply(im.getchannel('A'),band))
            put(sl,cx+((f%2)*6-3)*S)
    elif t<1.75:                                 # 3) rolling columns of stacked WICKETs
        roll=(t-1.1)*FPS*14*S
        for k in range(5):
            cx=BW*(0.1+0.2*k)
            step=WK_SM_C.height*0.62
            for j in range(-2,4):
                im=WK_SM_C if (j+k)%2==0 else WK_SM_W
                y=cy-step*j+(roll*(1 if k%2==0 else 0.8))%step - step/2
                bar.alpha_composite(im,(int(cx-im.width/2),int(y-im.height/2)))
    elif t<2.05:                                 # 4) one big WICKET pops
        p=ease(prog(t,1.75,1.9)); s=1.6-0.6*p
        im=WK_POP.resize((int(WK_POP.width*s),int(WK_POP.height*s)),Image.BILINEAR)
        put(im,BW/2)
    elif t<2.45:                                 # 5) WICKET WICKET WICKET
        gap=WK_TRI_W.width*1.12
        for k,im in enumerate((WK_TRI_W,WK_TRI_C,WK_TRI_W)):
            put(im,BW/2+(k-1)*gap)
    else:                                        # 6) fast marquee
        sp=(t-2.45)*FPS*38*S
        gap=WK_SM_W.width*1.12; x=-(sp%(gap*2))
        k=0
        while x<BW+gap:
            put(WK_SM_W if k%2==0 else WK_SM_C, x+gap/2); x+=gap; k+=1
    # light streak sweeping across at the start and before the marquee
    for (a,b) in ((0.05,0.45),(2.35,2.7)):
        p=prog(t,a,b)
        if 0<p<1:
            st=Image.new('L',(BW,BH),0); sd=ImageDraw.Draw(st)
            x=-BW*0.3+p*BW*1.6
            sd.polygon([(x,0),(x+60*S,0),(x+20*S,BH),(x-40*S,BH)],fill=170)
            st=st.filter(ImageFilter.GaussianBlur(10*S))
            pk=Image.new('RGBA',(BW,BH),PINK+(0,)); pk.putalpha(st); bar.alpha_composite(pk)
    # rounded bar mask, opening from the centre
    w_now=int(BW*vis); x0=(BW-w_now)//2
    m=Image.new('L',(BW,BH),0); ImageDraw.Draw(m).rounded_rectangle([x0,0,x0+w_now,BH],radius=6*S,fill=255)
    bar.putalpha(m)
    gl=Image.new('L',canvas.size,0); ImageDraw.Draw(gl).rounded_rectangle([BX0*S+x0,BY0*S,BX0*S+x0+w_now,BY1*S],radius=14*S,fill=int(140*vis))
    pass
    canvas.alpha_composite(bar,(BX0*S,BY0*S))
    return canvas.resize((W,H),Image.LANCZOS)

CROP=(BX0, BY0, BX1, BY1)

def to_gif_frames(frames):
    cw,ch=CROP[2]-CROP[0],CROP[3]-CROP[1]
    fr=[f.crop(CROP) for f in frames]
    bg=(24,20,44)
    flat=[]
    for f in fr:
        b=Image.new('RGB',f.size,bg); b.paste(f,(0,0),f); flat.append(b)
    big=Image.new('RGB',(cw,ch*8))
    for k in range(8): big.paste(flat[int(k*len(flat)/8)],(0,k*ch))
    pal=big.quantize(colors=200,method=Image.MEDIANCUT)
    out=[]
    for f,fl in zip(fr,flat):
        q=fl.quantize(palette=pal,dither=Image.Dither.NONE)
        pv=q.getpalette()[:600]+[0,0,0]*55+[0,255,0]
        q.putpalette(pv)
        mask=f.getchannel('A').point(lambda v:255 if v<110 else 0)
        q.paste(255,(0,0),mask)
        q.info['transparency']=255
        out.append(q)
    return out

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='sheet':
        ts=[0.2,0.5,0.95,1.3,1.85,2.2,2.7,3.2]
        sh=Image.new('RGB',(W,H*len(ts)))
        for k,tt in enumerate(ts): fr=frame(int(tt*FPS)); b=Image.new('RGB',(W,H),(54,57,63)); b.paste(fr,(0,0),fr); sh.paste(b,(0,k*H))
        sh.save(f'{OUT}_sheet.png')
    else:
        frames=[frame(i) for i in range(N)]
        # shared palette GIF
        q=to_gif_frames(frames)
        q[0].save(f'{OUT}.gif',save_all=True,append_images=q[1:],duration=int(1000/FPS),loop=0,optimize=False,disposal=2,transparency=255)
        frames=[Image.alpha_composite(Image.new('RGBA',f.size,(14,12,28,255)),f).convert('RGB') for f in frames]
        p=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-vf',f'scale=1000:{H}','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',f'{OUT}.mp4'],stdin=subprocess.PIPE)
        for f in frames: p.stdin.write(f.tobytes())
        p.stdin.close(); p.wait(); print('done')
