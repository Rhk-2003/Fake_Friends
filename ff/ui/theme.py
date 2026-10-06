"""Visual language: ink-purple night, hot pink -> amber accent, big rounded cards."""
import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700&display=swap');

:root{
  --ink:#120e1c; --panel:#1d1730; --panel2:#261e3f; --line:rgba(255,255,255,.09);
  --text:#f4efff; --muted:#a79fc0; --pink:#ff4d8d; --amber:#ffb347; --mint:#5ee6b0;
  --grad:linear-gradient(100deg,#ff4d8d 0%,#ff7a59 55%,#ffb347 100%);
}
html, body, [data-testid="stAppViewContainer"]{font-family:'DM Sans',system-ui,sans-serif;}
[data-testid="stAppViewContainer"]{
  background:
    radial-gradient(900px 480px at 12% -8%, rgba(255,77,141,.20), transparent 60%),
    radial-gradient(760px 460px at 105% 4%, rgba(255,179,71,.13), transparent 60%),
    var(--ink);
}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:2.2rem; padding-bottom:4rem;}
h1,h2,h3,h4{font-family:'Bricolage Grotesque','DM Sans',sans-serif !important; letter-spacing:-.02em;}

/* ---------- hero ---------- */
.ff-hero{text-align:center; padding:1.2rem 0 .4rem;}
.ff-hero .mask{font-size:3.2rem; line-height:1; display:inline-block; transform:rotate(-8deg);
  filter:drop-shadow(0 10px 24px rgba(255,77,141,.45));}
.ff-hero h1{font-size:clamp(2.9rem,11vw,5.2rem) !important; font-weight:800 !important;
  line-height:.92; margin:.3rem 0 .5rem; padding:0;}
.ff-hero h1 .fake{background:var(--grad); -webkit-background-clip:text; background-clip:text;
  color:transparent; display:inline-block; transform:rotate(-2.5deg);}
.ff-hero h1 .friends{color:transparent; -webkit-text-stroke:2px var(--text);}
.ff-hero .tag{color:var(--muted); font-size:1.12rem; margin:0;}
.ff-kicker{font-family:'Bricolage Grotesque',sans-serif; text-transform:uppercase;
  letter-spacing:.2em; font-size:.76rem !important; font-weight:700; color:var(--amber); margin:.6rem 0 .3rem;}
.ff-title{font-family:'Bricolage Grotesque',sans-serif; font-weight:800;
  font-size:clamp(1.85rem,7vw,2.5rem) !important; line-height:1.06; margin:0 0 .45rem; overflow-wrap:anywhere;}
.ff-sub{color:var(--muted); font-size:1.02rem; margin:0 0 1rem;}
/* player picker: stay two-up on phones instead of stacking */
.st-key-player_grid [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important; gap:.7rem;}
.st-key-player_grid [data-testid="stColumn"]{min-width:0 !important; flex:1 1 0 !important; width:auto !important;}

/* ---------- cards ---------- */
.ff-card{background:rgba(255,255,255,.045); border:1px solid var(--line); border-radius:22px;
  padding:1.25rem 1.35rem; margin:.6rem 0 1rem;}
.ff-q{background:linear-gradient(160deg,rgba(255,77,141,.16),rgba(255,179,71,.07) 70%), var(--panel);
  border:1px solid rgba(255,255,255,.12); border-radius:26px; padding:1.6rem 1.5rem; margin:.5rem 0 1.1rem;
  box-shadow:0 18px 50px -24px rgba(255,77,141,.55);}
.ff-q .emoji{font-size:2.4rem; line-height:1; margin-bottom:.5rem;}
.ff-q .text{font-family:'Bricolage Grotesque',sans-serif; font-weight:700;
  font-size:clamp(1.3rem,4.6vw,1.75rem); line-height:1.22; overflow-wrap:anywhere;}
.ff-meta{display:flex; flex-wrap:wrap; gap:.45rem; margin:.2rem 0 .7rem;}
.ff-chip{background:rgba(255,255,255,.07); border:1px solid var(--line); border-radius:999px;
  padding:.24rem .75rem; font-size:.84rem; color:var(--text); white-space:nowrap;}
.ff-chip b{color:var(--amber);}
.ff-chip.live{background:rgba(94,230,176,.14); border-color:rgba(94,230,176,.4); color:var(--mint);}
.ff-chip.draft{background:rgba(255,179,71,.12); border-color:rgba(255,179,71,.4); color:var(--amber);}

.ff-note{border-radius:18px; padding:1rem 1.15rem; margin:.7rem 0 1.1rem;
  background:rgba(255,179,71,.11); border:1px solid rgba(255,179,71,.45); border-left:6px solid var(--amber);}
.ff-note b{color:var(--amber);}
.ff-lock{display:flex; gap:.6rem; align-items:flex-start; color:var(--muted); font-size:.86rem;
  border:1px dashed var(--line); border-radius:14px; padding:.7rem .9rem; margin:1.1rem 0 .3rem;}
.ff-lock .i{font-size:1.05rem; line-height:1.3;}
.ff-banner{background:rgba(94,230,176,.1); border:1px solid rgba(94,230,176,.4); color:var(--mint);
  border-radius:14px; padding:.55rem .9rem; font-size:.9rem; margin-bottom:.8rem;}

/* ---------- answer reveal ---------- */
.ff-opt{display:flex; align-items:center; gap:.6rem; border-radius:16px; padding:.72rem 1rem;
  border:1px solid var(--line); margin-bottom:.5rem; color:var(--muted); background:rgba(255,255,255,.03);}
.ff-opt .t{flex:1; min-width:0; overflow-wrap:anywhere;}
.ff-opt .tagx{font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; white-space:nowrap;}
.ff-opt.ok{background:rgba(94,230,176,.16); border-color:var(--mint); color:var(--mint); font-weight:700;}
.ff-opt.bad{background:rgba(255,107,107,.14); border-color:#ff6b6b; color:#ffa3a3;}
.ff-verdict{font-family:'Bricolage Grotesque',sans-serif; font-weight:800; font-size:1.12rem; margin:.2rem 0 .7rem;}
.ff-verdict.ok{color:var(--mint);} .ff-verdict.bad{color:#ff8f8f;}
.ff-key{border-radius:18px; padding:1rem 1.15rem; margin:.6rem 0 1rem; background:rgba(94,230,176,.12);
  border:1px solid rgba(94,230,176,.5); border-left:6px solid var(--mint);}
.ff-key b{color:var(--mint);}
.ff-why{border-radius:18px; padding:.9rem 1.1rem; margin:.6rem 0 1rem; background:rgba(255,255,255,.05);
  border:1px solid var(--line);}
/* ---------- memories ---------- */
.ff-mem{background:rgba(255,255,255,.045); border:1px solid var(--line); border-radius:22px;
  padding:1.1rem 1.2rem; margin:.3rem 0 .4rem;}
.ff-mem .who{font-family:'Bricolage Grotesque',sans-serif; font-weight:800; font-size:1.2rem;}
.ff-mem .when{color:var(--muted); font-size:.8rem; margin-bottom:.7rem;}
.ff-mem .lbl{font-size:.72rem; letter-spacing:.14em; text-transform:uppercase; color:var(--amber);
  font-weight:700; margin-top:.75rem;}
.ff-mem .txt{overflow-wrap:anywhere; white-space:pre-wrap;}

/* ---------- avatars / players ---------- */
.ff-av{border-radius:50%; object-fit:cover; display:inline-flex; align-items:center; justify-content:center;
  background:var(--panel2); border:2px solid rgba(255,255,255,.16); font-family:'Bricolage Grotesque',sans-serif;
  font-weight:800; color:var(--text); flex:none;}
.ff-player{text-align:center; background:rgba(255,255,255,.045); border:1px solid var(--line);
  border-radius:22px; padding:1.1rem .6rem .8rem; margin-bottom:.5rem;}
.ff-player .name{font-family:'Bricolage Grotesque',sans-serif; font-weight:700; font-size:1.08rem;
  margin-top:.55rem; overflow-wrap:anywhere;}
.ff-player .tag{font-size:.78rem; color:var(--muted); min-height:1.5rem; line-height:1.5rem;}

/* ---------- result ---------- */
.ff-result{text-align:center; border-radius:28px; padding:1.7rem 1.2rem 1.4rem; margin:.4rem 0 1rem;
  background:linear-gradient(170deg,rgba(255,77,141,.22),rgba(255,179,71,.08) 65%), var(--panel);
  border:1px solid rgba(255,255,255,.14);}
.ff-result .who{font-family:'Bricolage Grotesque',sans-serif; font-weight:700; font-size:1.25rem; margin-top:.5rem;}
.ff-result .score{font-family:'Bricolage Grotesque',sans-serif; font-weight:800; font-size:clamp(3.4rem,15vw,5rem);
  line-height:1; background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent;}
.ff-result .score small{font-size:.42em; color:var(--muted); -webkit-text-fill-color:var(--muted);}
.ff-result .pct{font-size:1.1rem; color:var(--muted); margin-bottom:.8rem;}
.ff-result .react{font-size:1.05rem; margin-top:.9rem;}

/* ---------- podium / leaderboard ---------- */
.ff-podium{display:flex; align-items:flex-end; justify-content:center; gap:.6rem; margin:1.2rem 0 .4rem;}
.ff-podium .col{flex:1 1 0; min-width:0; max-width:190px; text-align:center;}
.ff-podium .pname{font-family:'Bricolage Grotesque',sans-serif; font-weight:700; margin:.35rem 0 .1rem;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.ff-podium .pscore{font-size:.86rem; color:var(--muted); margin-bottom:.4rem;}
.ff-podium .block{border-radius:18px 18px 6px 6px; display:flex; flex-direction:column; align-items:center;
  justify-content:flex-start; padding-top:.6rem; font-family:'Bricolage Grotesque',sans-serif; font-weight:800;}
.ff-podium .block .medal{font-size:1.7rem; line-height:1;}
.ff-podium .block .place{font-size:.8rem; letter-spacing:.14em; opacity:.85;}
.ff-podium .p1 .block{height:150px; background:linear-gradient(180deg,#ffd76a,#e89a1c); color:#3a2300;}
.ff-podium .p2 .block{height:112px; background:linear-gradient(180deg,#e6e9f2,#9aa3b8); color:#1e2333;}
.ff-podium .p3 .block{height:84px; background:linear-gradient(180deg,#f0a878,#b5622d); color:#2e1503;}
.ff-podium .empty .block{background:rgba(255,255,255,.05); color:var(--muted); border:1px dashed var(--line);}
.ff-row{display:flex; align-items:center; gap:.75rem; padding:.55rem .8rem; border-radius:16px;
  background:rgba(255,255,255,.04); border:1px solid var(--line); margin-bottom:.4rem;}
.ff-row.me{border-color:var(--pink); background:rgba(255,77,141,.12);}
.ff-row .rk{font-family:'Bricolage Grotesque',sans-serif; font-weight:800; width:2rem; text-align:center; color:var(--amber);}
.ff-row .nm{flex:1; min-width:0; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.ff-row .sc{font-family:'Bricolage Grotesque',sans-serif; font-weight:700; white-space:nowrap;}
.ff-row .sc small{color:var(--muted); font-weight:400; font-family:'DM Sans',sans-serif;}
.ff-row.dim{opacity:.62;}

/* ---------- streamlit widgets ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button{
  border-radius:16px; font-weight:700; padding:.62rem 1rem; border:1px solid rgba(255,255,255,.16);
  transition:transform .12s ease, box-shadow .12s ease;}
.stButton > button:hover, .stFormSubmitButton > button:hover{transform:translateY(-1px);}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"]{
  background:var(--grad) !important; border:0 !important; color:#1a0b12 !important;
  box-shadow:0 10px 26px -12px rgba(255,77,141,.8);}
[data-testid="stBaseButton-primary"]:disabled, [data-testid="stBaseButton-primaryFormSubmit"]:disabled{
  background:rgba(255,255,255,.07) !important; color:var(--muted) !important; box-shadow:none;
  border:1px dashed var(--line) !important;}
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primaryFormSubmit"] p{font-weight:800;}
[data-testid="stProgress"] > div > div > div > div{background:var(--grad);}
[data-testid="stForm"]{border-radius:20px; border-color:var(--line); background:rgba(255,255,255,.03);}
[data-testid="stExpander"] details{border-radius:16px; border-color:var(--line);}
[data-testid="stMetric"]{background:rgba(255,255,255,.045); border:1px solid var(--line);
  border-radius:16px; padding:.7rem .9rem;}
video{border-radius:18px;}
@media (max-width:640px){ .block-container{padding-left:1rem; padding-right:1rem;} .ff-row .sc .x{display:none;} }
@media (prefers-reduced-motion:reduce){ .stButton > button{transition:none;} }
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
