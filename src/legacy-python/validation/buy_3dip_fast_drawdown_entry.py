"""Test accelerated entries only when QQQ's 20-day drawdown is rapid."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))
from buy_3dip_parameter_search import _trade, load_prices  # noqa: E402

TARGETS = (0.70, 0.87, 0.90, 1.00)
B, C, D, G1, R2, R3 = (0.10, 0.20, 0.325, 0.075, 0.085, 0.175)


@dataclass(frozen=True)
class Profile:
    roc20_trigger: float
    entry_scale: float


def simulate(data, profile):
    q_open, q_close = data.QQQ_Open.to_numpy(float), data.QQQ_Close.to_numpy(float)
    b_open, b_close = data.BIL_Open.to_numpy(float), data.BIL_Close.to_numpy(float)
    roc20 = data.QQQ_ROC20.to_numpy(float)
    values = np.empty(len(data)); cash, q_shares, b_shares = 1.0, 0.0, 0.0
    stage, peak, pending, transitions = 0, 0.0, 0, 0
    for i in range(len(data)):
        if pending is not None:
            total = cash + q_shares*q_open[i] + b_shares*b_open[i]; target = TARGETS[pending]
            qd, bd = total*target/q_open[i]-q_shares, total*(1-target)/b_open[i]-b_shares
            if qd < 0: cash, q_shares = _trade(cash,q_shares,q_open[i],qd)
            if bd < 0: cash, b_shares = _trade(cash,b_shares,b_open[i],bd)
            if qd > 0: cash, q_shares = _trade(cash,q_shares,q_open[i],qd)
            if bd > 0: cash, b_shares = _trade(cash,b_shares,b_open[i],bd)
            pending = None
        scale = profile.entry_scale if np.isfinite(roc20[i]) and roc20[i] <= profile.roc20_trigger else 1.0
        old, price = stage, q_close[i]
        if stage == 3 and price >= peak*(1-R3): stage=2
        elif stage == 2 and price <= peak*(1-D*scale): stage=3
        elif stage == 2 and price >= peak*(1-R2): stage=1
        elif stage == 1 and price <= peak*(1-C*scale): stage=2
        elif stage == 1 and price >= peak*(1+G1): stage=0
        elif stage == 0 and peak > 0 and price <= peak*(1-B*scale): stage=1
        changed = stage != old
        if peak == 0: peak=price
        elif changed and stage == 0: peak=price
        elif stage == 0 and price > peak: peak=price
        if i == 0 or changed: pending=stage; transitions += int(changed)
        values[i] = cash + q_shares*q_close[i] + b_shares*b_close[i]
    years=(data.index[-1]-data.index[0]).days/365.25; cagr=(values[-1]/values[0])**(1/years)-1
    mdd=float((values/np.maximum.accumulate(values)-1).min())
    return {"CAGR":cagr,"MDD":mdd,"Calmar":cagr/abs(mdd),"Transitions":transitions}


def main():
    base=load_prices(); qqq=pd.read_csv(MODULE_DIR.parents[1]/"data"/"QQQ.csv",index_col="Date",parse_dates=True,usecols=["Date","ROC20"])
    data=base.join(qqq.add_prefix("QQQ_"),how="left")
    baseline=simulate(data,Profile(-1.0,1.0)); rows=[]
    for trigger in (-0.08,-0.12,-0.16,-0.20):
        for scale in (0.70,0.80,0.90): rows.append({**Profile(trigger,scale).__dict__,**simulate(data,Profile(trigger,scale))})
    result=pd.DataFrame(rows)
    for metric in ("CAGR","MDD","Calmar"): result[f"{metric}_vs_21"]=result[metric]-baseline[metric]
    print("baseline",baseline); print(result.sort_values(["Calmar","CAGR"],ascending=False).to_string(index=False))

if __name__=="__main__": main()
