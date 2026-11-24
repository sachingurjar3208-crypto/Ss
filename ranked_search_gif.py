"""Season 2 "RANK UP MODE" searching GIF (radar + tier badge), shown while rcmp ranked looks for a player.

    data = make_search_gif("Gold", 5)        # bytes, cached per tier
    file = await search_file("Gold", 5)      # discord.File (or None if anything fails)

900x420, 2 s seamless loop, 20 fps. Colours follow the player's tier (ranked_start_gif.TIER_COL).
"""
from __future__ import annotations

import asyncio
import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter

import ranked_start_gif as rg

W,H,S,FPS,N = 900,420,2,20,40           # 2 s seamless loop
NAME,LAB = rg._NAME, rg._LABEL
F = rg._font
GOLD=(255,211,107); ICE=(110,210,255)
def bg_static(col):
    w,h=W*S,H*S
    im=Image.new("RGB",(w,h)); d=ImageDraw.Draw(im)
    for y in range(h):
        t=y/h; d.line([(0,y),(w,y)],fill=(int(6+8*t),int(12+14*t),int(34+26*t)))
    g=Image.new("L",(w//4,h//4),0); ImageDraw.Draw(g).ellipse([w/4*.25,h/4*.05,w/4*.75,h/4*1.05],fill=110)
    im.paste(ICE,(0,0),g.filter(ImageFilter.GaussianBlur(30)).resize((w,h),Image.BICUBIC).point(lambda v:int(v*.55)))
    g=Image.new("L",(w//4,h//4),0); ImageDraw.Draw(g).ellipse([w/4*.38,h/4*.2,w/4*.62,h/4*.8],fill=120)
    im.paste(col,(0,0),g.filter(ImageFilter.GaussianBlur(26)).resize((w,h),Image.BICUBIC).point(lambda v:int(v*.5)))
    # crystal shards on both sides (static, Season 2 look)
    random.seed(5); sh=Image.new("L",(w,h),0); sd=ImageDraw.Draw(sh); edge=Image.new("L",(w,h),0); ed=ImageDraw.Draw(edge)
    for side in (-1,1):
        cx=w/2+side*w*0.43; cy=h*0.5
        for k in range(9):
            a=math.radians((180 if side<0 else 0)+random.uniform(-70,70)); L_=random.uniform(120,260)*S; wd=random.uniform(20,48)*S
            bx,by=cx+random.uniform(-30,30)*S,cy+random.uniform(-60,60)*S
            tx,ty=bx-math.cos(a)*L_*-1*(-1),by+math.sin(a)*L_
            tx,ty=bx+math.cos(a)*L_,by+math.sin(a)*L_
            nx,ny=-math.sin(a),math.cos(a)
            pts=[(bx+nx*wd*.3,by+ny*wd*.3),(bx+(tx-bx)*.5+nx*wd,by+(ty-by)*.5+ny*wd),(tx,ty),(bx+(tx-bx)*.5-nx*wd*.7,by+(ty-by)*.5-ny*wd*.7),(bx-nx*wd*.3,by-ny*wd*.3)]
            sd.polygon(pts,fill=random.randint(70,140)); ed.polygon(pts,outline=150,width=S)
    im.paste((60,150,230),(0,0),sh); im.paste((200,240,255),(0,0),edge)
    # hairlines
    d=ImageDraw.Draw(im)
    for x in range(0,w,2):
        a=math.sin(math.pi*x/w)
        d.point((x,0),fill=tuple(int(c*a) for c in GOLD)); d.point((x,h-1),fill=tuple(int(c*a) for c in GOLD))
    return im

def frame(i,base,col,tier,overs):
    t=i/N; w,h=W*S,H*S; cx,cy=w//2,int(h*0.505)
    im=base.copy(); d=ImageDraw.Draw(im,"RGBA")
    # pulsing rings (3, staggered, seamless)
    for k in range(3):
        u=(t+k/3)%1
        r=(70+150*u)*S
        d.ellipse([cx-r,cy-r,cx+r,cy+r],outline=(*ICE,int(200*(1-u))),width=3*S)
    # radar disc + sweep
    R=112*S
    d.ellipse([cx-R,cy-R,cx+R,cy+R],fill=(8,20,48,170),outline=(*ICE,140),width=2*S)
    for rr in (R*.66,R*.33): d.ellipse([cx-rr,cy-rr,cx+rr,cy+rr],outline=(*ICE,55),width=S)
    d.line([cx-R,cy,cx+R,cy],fill=(*ICE,40),width=S); d.line([cx,cy-R,cx,cy+R],fill=(*ICE,40),width=S)
    ang=t*360
    sweep=Image.new("L",(w,h),0); sw=ImageDraw.Draw(sweep)
    for k in range(40):
        sw.pieslice([cx-R,cy-R,cx+R,cy+R],ang-90-k*1.6,ang-90-k*1.6+1.8,fill=int(150*(1-k/40)))
    im.paste(ICE,(0,0),sweep); d=ImageDraw.Draw(im,"RGBA")
    a=math.radians(ang-90); d.line([cx,cy,cx+R*math.cos(a),cy+R*math.sin(a)],fill=(220,245,255,230),width=3*S)
    # blips that light when the sweep passes
    for bx,by,ba in ((0.55,-0.4,30),(-0.6,0.25,200),(0.2,0.65,120),(-0.3,-0.6,300)):
        bang=(math.degrees(math.atan2(by,bx))+90)%360
        dlt=(ang-bang)%360; lum=max(0,1-dlt/120)
        px,py=cx+bx*R*.8,cy+by*R*.8; rr=5*S*(1+lum)
        d.ellipse([px-rr,py-rr,px+rr,py+rr],fill=(*GOLD,int(60+190*lum)))
    # tier emblem in the middle
    em=rg._emblem(col); sc=1.05+0.04*math.sin(t*2*math.pi)
    em=em.resize((int(em.width*sc),int(em.height*sc)),Image.LANCZOS)
    glow=Image.new("L",(w,h),0); ImageDraw.Draw(glow).ellipse([cx-60*S,cy-60*S,cx+60*S,cy+60*S],fill=150)
    im.paste(col,(0,0),glow.filter(ImageFilter.GaussianBlur(18*S)).point(lambda v:int(v*(.6+.25*math.sin(t*2*math.pi)))))
    im.paste(em,(cx-em.width//2,cy-em.height//2),em); d=ImageDraw.Draw(im,"RGBA")
    # header
    d.text((cx,30*S),"RANK UP MODE",font=F(NAME,34*S),fill=(255,255,255),anchor="mm")
    pill="SEASON 2"; pf=F(LAB,20*S); pw=pf.getlength(pill)+36*S
    d.rounded_rectangle([cx-pw/2,52*S,cx+pw/2,80*S],radius=14*S,fill=(*GOLD,255))
    d.text((cx,66*S),pill,font=pf,fill=(30,18,0),anchor="mm")
    # searching text with animated dots
    dots="."*(1+int(t*3)%3 if True else 0)
    st="SEARCHING FOR A PLAYER"
    sf=F(LAB,30*S); sw_=sf.getlength(st)
    d.text((cx-sw_/2-8*S, h-74*S), st, font=sf, fill=(225,240,255), anchor="lm")
    for k in range(3):
        on=k <= int(t*3)%3
        d.ellipse([cx+sw_/2-2*S+k*14*S, h-78*S, cx+sw_/2+6*S+k*14*S, h-70*S], fill=(*GOLD,255 if on else 60))
    # info chips
    chips=[(tier.upper(),col),(f"{overs} OVERS",ICE),("WIN +1  ·  LOSS -1",(200,200,230))]
    cf=F(LAB,20*S); widths=[cf.getlength(c)+30*S for c,_ in chips]; tot=sum(widths)+16*S*(len(chips)-1)
    x=cx-tot/2; y=h-42*S
    for (c,cc),wd in zip(chips,widths):
        d.rounded_rectangle([x,y-15*S,x+wd,y+15*S],radius=15*S,outline=(*cc,230),width=2*S,fill=(10,18,40,200))
        d.text((x+wd/2,y),c,font=cf,fill=(*cc,255),anchor="mm"); x+=wd+16*S
    return im.reduce(S)

def make_search_gif(tier: str = "Bronze", overs: int = 5) -> bytes:
    col = rg.TIER_COL.get(tier, GOLD)
    base = bg_static(col)
    frames = [frame(i, base, col, tier, overs) for i in range(N)]
    sheet = Image.new("RGB", (W, H * 4))
    for k, i in enumerate((0, 10, 20, 30)):
        sheet.paste(frames[i], (0, k * H))
    pal = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pics = [f.quantize(palette=pal) for f in frames]
    buf = io.BytesIO()
    pics[0].save(buf, format="GIF", save_all=True, append_images=pics[1:], duration=int(1000 / FPS), loop=0, disposal=1)
    return buf.getvalue()


_cache: dict[tuple[str, int], bytes] = {}


def get_search_gif(tier: str, overs: int) -> bytes:
    key = (tier, int(overs))
    if key not in _cache:
        _cache[key] = make_search_gif(tier, overs)
    return _cache[key]


async def search_file(tier: str, overs: int):
    """discord.File for the searching card, or None (never blocks a search)."""
    try:
        import discord
        data = await asyncio.to_thread(get_search_gif, tier, overs)
        return discord.File(io.BytesIO(data), filename="ranked_search.gif")
    except Exception as e:
        print(f"[ranked_search_gif] skipped ({type(e).__name__}: {e})")
        return None


async def warm(overs: int = 5):
    """Build every tier's GIF in the background once, so the first search is instant."""
    for tier in rg.TIER_COL:
        try:
            await asyncio.to_thread(get_search_gif, tier, overs)
        except Exception as e:
            print(f"[ranked_search_gif] warm {tier} failed: {type(e).__name__}: {e}")
