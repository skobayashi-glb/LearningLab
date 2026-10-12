"use strict";
const A={pollMs:5000,pad:35,maxCost:20,font:11,point:6,scoreFloor:0,scoreCeiling:1,percent:100,
  roles:{attacker:"火力",tank:"壁",status:"状態付与",positioning:"位置操作",support:"支援"},
  strengths:{weak:"弱め",standard:"標準",strong:"強め",conditional:"編成依存"},
  colors:{attacker:"#d69b32",tank:"#218879",status:"#7376b6",positioning:"#479ab2",support:"#a36888"}};
let lastAnalysisPoll=0, analysisData=null;
function percentage(value){return value==null?"未測定":`${fmt(value*A.percent,1)}%`;}
function renderCharacterFeedback(){
  const body=$("character-feedback");if(!body)return;body.replaceChildren();
  if(status.learning_scope!=="per-character"){$("character-learning").hidden=true;return;}
  $("character-learning").hidden=false;
  const history=status.history, descriptions=status.distribution?.per_character||{};
  for(const character of Object.keys(status.baseline||{})){
    const metadata=status.character_metadata[character]||{}, scores=history.map(t=>t.character_scores?.[character]).filter(Number.isFinite);
    const row=document.createElement("tr");[character,A.strengths[metadata.strength],metadata.cost,fmt(scores[0]),fmt(scores.at(-1)),scores.length?fmt(Math.min(...scores)):"—",descriptions[character]?.updates||0].forEach(v=>row.append(cell("td",v)));body.append(row);
  }
}
async function refreshAnalysis(){
  if(status.evaluation_mode!=="budget-wave"){$("roster").hidden=true;return;}
  $("roster").hidden=false;const progress=status.analysis_progress;
  $("analysis-progress").textContent=progress?`比較測定 ${progress.completed} / ${progress.total} 試合`:"比較測定を待っています";
  if(Date.now()-lastAnalysisPoll<A.pollMs)return;lastAnalysisPoll=Date.now();
  try{const data=await api("/api/analysis");if(!data)return;analysisData=data;renderAnalysis(data);}catch(error){console.error(error);}
}
function renderAnalysis(data){
  $("analysis-context").textContent=`両軍の合計コスト ${data.budget} · ${data.parameter_version==="baseline"?"設計基準版":"採用版"} · ${data.partial?"測定途中":data.scope==="focused-only"?"キャラ中心編成の測定完了（ペア・採用率は後で測定）":"測定完了"} · 相手は固定した基準版 · 両陣営を入れ替えて比較 · ${data.completed}試合 · 分析専用seed（学習・採否には使いません）`;
  drawCostChart(data);const body=$("strength-rows");body.replaceChildren();
  for(const s of data.strengths){const row=document.createElement("tr"),m=status.character_metadata[s.character]||{};
    [s.character,`${s.cost} / ${A.roles[m.role]||m.role||"—"}`,fmt(s.score),`${percentage(s.win_rate)}${s.win_interval?" / "+s.win_interval.map(percentage).join("〜"):""}`,s.trials].forEach(v=>row.append(cell("td",v)));body.append(row);}
  const bars=$("adoption-bars");bars.replaceChildren();for(const item of data.adoption){const line=document.createElement("div");line.className="adoption-row";line.append(cell("span",item.character));const track=document.createElement("div");track.className="adoption-track";const fill=document.createElement("i");fill.style.width=`${(item.rate||0)*A.percent}%`;track.append(fill);line.append(track,cell("b",percentage(item.rate)));line.title=`比較条件 ${item.contexts}件。予算を埋める相方として含まれた場合も計上`;bars.append(line);}
  heatmap("matchup-map",data,false);heatmap("synergy-map",data,true);
}
function drawCostChart(data){const canvas=$("cost-chart"),ctx=canvas.getContext("2d"),w=canvas.width,h=canvas.height,p=A.pad;
  const x=v=>p+v/A.maxCost*(w-2*p), y=v=>h-p-v*(h-2*p);ctx.clearRect(0,0,w,h);ctx.font=`${A.font}px ${C.font}`;
  for(const score of [0,.25,.5,.75,1]){ctx.strokeStyle=C.colors.grid;ctx.beginPath();ctx.moveTo(p,y(score));ctx.lineTo(w-p,y(score));ctx.stroke();ctx.fillStyle=C.colors.muted;ctx.fillText(fmt(score,2),0,y(score)+C.chartLabelOffset);}
  for(const cost of [1,5,10,15,20]){ctx.fillStyle=C.colors.muted;ctx.fillText(String(cost),x(cost),h-C.chartBottomOffset);}
  ctx.fillText("コスト",w-p-C.chartRightLabelWidth,h-C.chartBottomOffset);
  for(const item of data.strengths){if(item.score==null)continue;const role=status.character_metadata[item.character]?.role;ctx.fillStyle=A.colors[role]||C.colors.ink;ctx.beginPath();ctx.arc(x(item.cost),y(item.score),A.point,0,2*Math.PI);ctx.fill();ctx.fillStyle=C.colors.ink;ctx.fillText(item.character,x(item.cost)+A.point,y(item.score)-A.point);}
}
function heatmap(id,data,synergy){const table=document.createElement("table"),head=document.createElement("tr");head.append(cell("th",synergy?"行 × 相方":"行 → 相手"));data.characters.forEach(c=>head.append(cell("th",c)));table.append(head);
  for(const left of data.characters){const row=document.createElement("tr");row.append(cell("th",left));for(const right of data.characters){let item,value=null;
    if(synergy){item=data.synergy.find(s=>(s.left===left&&s.right===right)||(s.right===left&&s.left===right));value=item?.delta;}else{item=data.matchups.find(m=>m.character===left&&m.opponent===right);value=item?.score;}
    const td=cell("td",value==null?"—":`${synergy&&value>0?"+":""}${fmt(value,2)}`);if(value!=null){const centered=synergy?value:value-.5;td.style.backgroundColor=centered>=0?`rgba(33,136,121,${Math.min(1,Math.abs(centered)*2)*.6})`:`rgba(220,105,105,${Math.min(1,Math.abs(centered)*2)*.6})`;}
    td.title=item?`${item.trials}試合${item.reason?" / "+item.reason:""} · 合計コスト${data.budget}`:"未試験または同一キャラ";row.append(td);}table.append(row);}
  $(id).replaceChildren(table);
}
