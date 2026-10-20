/* HK PropPulse v7.2 前端补丁（放在仓库根目录，并在 index.html 的 </body> 前加一行：
     <script src="weights_patch.js"></script>
   作用：
   1) 第 1 页四个权重滑杆联动：调任意一个，其余三个按原比例自动增减，合计永远 = 100%。
   2) 显示「合计 100%」与各项实际占比。
   3) 筛选引擎的盘源若来自每日自动生成（curated_deals.auto），标题/计数改为如实显示生成日期。
*/
(function () {
  const IDS = ["wRngDisc", "wRngYield", "wRngUpside", "wRngSafety"];
  const LBL = ["wLblDisc", "wLblYield", "wLblUpside", "wLblSafety"];
  const els = IDS.map(id => document.getElementById(id));
  if (els.some(e => !e)) return;
  els.forEach(e => { e.min = 0; e.max = 100; e.step = 1; });

  // 初始化：把现有值按比例换算成合计 100
  let prev = normalise(els.map(e => parseFloat(e.value) || 0));
  apply(prev);

  function normalise(v) {
    const s = v.reduce((a, b) => a + b, 0) || 1;
    const out = v.map(x => Math.round(x / s * 100));
    out[out.indexOf(Math.max(...out))] += 100 - out.reduce((a, b) => a + b, 0);   // 修正四舍五入误差
    return out;
  }
  function apply(v) {
    v.forEach((x, i) => { els[i].value = x; const l = document.getElementById(LBL[i]); if (l) l.textContent = x + "%"; });
    let tot = document.getElementById("wTotalNote");
    if (!tot) {
      tot = document.createElement("div");
      tot.id = "wTotalNote";
      tot.style.cssText = "font-size:0.74rem;color:var(--text-secondary);margin-top:0.35rem;";
      els[3].closest("div").parentNode.appendChild(tot);
    }
    tot.textContent = `权重合计 ${v.reduce((a, b) => a + b, 0)}%（调整任一项，其余三项按比例自动增减）`;
  }
  function rebalance() {
    const cur = els.map(e => parseFloat(e.value) || 0);
    const k = cur.findIndex((x, i) => x !== prev[i]);
    if (k < 0) { apply(prev); return; }
    const fixed = Math.min(100, cur[k]);
    const others = prev.map((x, i) => (i === k ? 0 : x));
    const sumOthers = others.reduce((a, b) => a + b, 0);
    const remain = 100 - fixed;
    let next = prev.map((x, i) => (i === k ? fixed : (sumOthers ? x / sumOthers * remain : remain / 3)));
    next = next.map(Math.round);
    const diff = 100 - next.reduce((a, b) => a + b, 0);
    const j = next.findIndex((x, i) => i !== k && x + diff >= 0);
    if (j >= 0) next[j] += diff;
    prev = next;
    apply(next);
  }

  // 覆盖原函数：先联动，再重算排序
  window.recalcExpertScores = function () {
    rebalance();
    if (typeof renderDealsList === "function") renderDealsList();
  };

  // 计数与标题如实显示数据来源
  const _render = window.renderDealsList;
  window.renderDealsList = function () {
    _render();
    const auto = typeof curatedDeals !== "undefined" && curatedDeals.length && curatedDeals[0].auto;
    const badge = document.getElementById("dealCountBadge");
    if (badge && auto) badge.textContent = badge.textContent.replace("核验真实盘源", `个每日自动生成盘源（${curatedDeals[0].generated}）`).replace("个个", "个");
    if (badge && !auto) badge.textContent += " · ⚠ 静态样本，未每日更新";
  };
  document.querySelectorAll(".section-title").forEach(t => {
    if (t.textContent.includes("多维权重筛选引擎")) {
      t.textContent = "🎯 多维权重筛选引擎（盘源每日由实盘库自动生成 · 四维分数由数据计算）";
    }
  });
  window.renderDealsList();
})();
