"""Crea un finto progetto Logic con tracce sintetiche, per provare BlackMix.

Uso: python3 strumenti/crea_progetto_prova.py ["Canzone Prova.logicx"]
"""
import numpy as np, struct, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from blackmix.audio_io import write_wav
sr=48000; dur=40
OUT = sys.argv[1] if len(sys.argv) > 1 else 'Canzone Prova.logicx'
root = os.path.join(OUT, 'Media', 'Audio Files')
os.makedirs(root, exist_ok=True)
rng=np.random.default_rng(3)
t=np.arange(sr*dur)/sr
bpm=100; beat=60/bpm
def env(times, dec, n):
    e=np.zeros(n)
    for tt in times:
        i=int(tt*sr); k=np.arange(n-i)/sr; e[i:]+=np.exp(-k/dec)
    return e
beats=np.arange(0,dur,beat)
kick=np.sin(2*np.pi*(50+80*np.exp(-t*0))*t)  # placeholder
# kick: decaying sine sweep per hit
kick=np.zeros_like(t)
for b in beats[::2]:
    i=int(b*sr); k=np.arange(min(int(0.4*sr),t.size-i))/sr
    kick[i:i+k.size]+=np.sin(2*np.pi*(55*k+60*(1-np.exp(-k*30))/30))*np.exp(-k/0.12)
snare=np.zeros_like(t)
for b in beats[1::2]:
    i=int(b*sr); k=np.arange(min(int(0.3*sr),t.size-i))/sr
    snare[i:i+k.size]+=(0.6*rng.standard_normal(k.size)*np.exp(-k/0.08)+0.5*np.sin(2*np.pi*190*k)*np.exp(-k/0.05))
notes=[55,55,73.4,65.4]
bass=np.zeros_like(t)
for j,b in enumerate(np.arange(0,dur,beat*4)):
    i=int(b*sr); k=np.arange(min(int(beat*4*sr),t.size-i))/sr; f=notes[j%4]
    bass[i:i+k.size]+=sum(np.sin(2*np.pi*f*h*k)/h for h in range(1,6))*np.exp(-k/2)
gtr=np.zeros_like(t)
for j,b in enumerate(np.arange(0,dur,beat*4)):
    i=int(b*sr); k=np.arange(min(int(beat*4*sr),t.size-i))/sr; f=notes[j%4]*4
    gtr[i:i+k.size]+=sum(np.sin(2*np.pi*f*r*h*k)/h for r in (1,1.26,1.5) for h in range(1,12))*np.exp(-k/1.5)
voice=np.zeros_like(t)
for j,b in enumerate(np.arange(8,dur,beat*2)):
    i=int(b*sr); k=np.arange(min(int(beat*1.8*sr),t.size-i))/sr; f=220*2**((j%5)/12)
    vib=f*(1+0.01*np.sin(2*np.pi*5.5*k))
    ph=2*np.pi*np.cumsum(vib)/sr
    v=sum(np.sin(h*ph)*np.exp(-((h*f-700)/600)**2 - 0*h)+0.3*np.sin(h*ph)*np.exp(-((h*f-2600)/500)**2) for h in range(1,30))
    s=rng.standard_normal(k.size)*np.exp(-((k-0.05)/0.03)**2)*0.6
    voice[i:i+k.size]+=(v*np.minimum(1,k/0.05)*np.minimum(1,(k[-1]-k)/0.05)+s)
def norm(x,db): return x/np.abs(x).max()*10**(db/20)
def bwf(path,x,sr,timeref):
    write_wav(path,x,sr,24)
    data=open(path,'rb').read()
    bext=bytearray(602); bext[338:346]=struct.pack('<II',timeref&0xffffffff,timeref>>32)
    chunk=b'bext'+struct.pack('<I',len(bext))+bytes(bext)
    new=data[:12]+chunk+data[12:]
    new=new[:4]+struct.pack('<I',len(new)-8)+new[8:]
    open(path,'wb').write(new)
T0=3600*sr  # Logic parte da 01:00:00:00
bwf(f'{root}/Kick#01.wav',norm(kick,-6)[:,None],sr,T0)
bwf(f'{root}/Snare#01.wav',norm(-snare,-8)[:,None],sr,T0)
# basso registrato dopo: parte a 2 s
bwf(f'{root}/Basso#01.wav',norm(bass[2*sr:],-10)[:,None],sr,T0+2*sr)
# take vecchia del basso
bwf(f'{root}/Basso#00.wav',norm(bass[2*sr:],-10)[:,None]*0.5,sr,T0+2*sr)
# chitarre: due take in L/R come file separati, una e' AIFF 44.1 senza timestamp
bwf(f'{root}/Gtr L#01.wav',norm(gtr,-12)[:,None],sr,T0)
from scipy.signal import resample_poly
gr = norm(gtr*0.9+0.02*rng.standard_normal(t.size), -12)
# seconda chitarra: 44.1 kHz e senza timestamp (come un file importato)
write_wav(f'{root}/Gtr R.wav', resample_poly(gr, 147, 160)[:, None], 44100)
# voce parte a 8 s
bwf(f'{root}/Lead Vox#03.wav',norm(voice[8*sr:],-4)[:,None],sr,T0+8*sr)
# file generico non riconoscibile dal nome
bwf(f'{root}/Audio 7#01.wav',np.zeros((sr*5,1)),sr,T0)
print(sorted(os.listdir(root)))
