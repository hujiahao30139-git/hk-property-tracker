/* HK PropPulse v7.3 前端补丁 —— 放在仓库根目录，index.html 的 </body> 前加：
     <script src="weights_patch.js"></script>
   1) 四个权重滑杆联动，合计永远 = 100%
   2) 云端数据载入后，自动重绘第 1 页筛选引擎（修正：之前只在页面打开那一刻显示一次）
   3) 计数旁如实显示：数据来自哪个文件、哪天生成；读不到云端数据时直接显示原因
*/
(function () {
  const IDS = ["wRngDisc", "wRngYield", "wRngUpside", "wRngSafety"];
  const LBL = ["wLblDisc", "wLblYield", "wLblUpside", "wLblSafety"];
  const els = IDS.map(id => document.getElementById(id));
  let loadInfo = "云端数据载入中…";

  // ---------- 1. 权重联动 ----------
  if (els.every(Boolean)) {
    els.forEach(e => { e.min = 0; e.max = 100; e.step = 1; });
    const normalise = v => {
      const s = v.reduce((a, b) => a + b, 0) || 1;
      const out = v.map(x => Math.round(x / s * 100));
      out[out.indexOf(Math.max(...out))] += 100 - out.reduce((a, b) => a + b, 0);
      return out;
    };
    let prev = normalise(els.map(e => parseFloat(e.value) || 0));
    const apply = v => {
      v.forEach((x, i) => { els[i].value = x; const l = document.getElementById(LBL[i]); if (l) l.textContent = x + "%"; });
      let tot = document.getElementById("wTotalNote");
      if (!tot) {
        tot = document.createElement("div");
        tot.id = "wTotalNote";
        tot.style.cssText = "font-size:0.74rem;color:var(--text-secondary);margin-top:0.35rem;";
        els[3].closest("div").parentNode.appendChild(tot);
      }
      tot.textContent = `权重合计 ${v.reduce((a, b) => a + b, 0)}%（调整任一项，其余三项按比例自动增减）`;
    };
    apply(prev);
    window.recalcExpertScores = function () {
      const cur = els.map(e => parseFloat(e.value) || 0);
      const k = cur.findIndex((x, i) => x !== prev[i]);
      if (k >= 0) {
        const fixed = Math.min(100, cur[k]);
        const sumOthers = prev.reduce((a, x, i) => a + (i === k ? 0 : x), 0);
        let next = prev.map((x, i) => (i === k ? fixed : (sumOthers ? x / sumOthers * (100 - fixed) : (100 - fixed) / 3))).map(Math.round);
        const diff = 100 - next.reduce((a, b) => a + b, 0);
        const j = next.findIndex((x, i) => i !== k && x + diff >= 0);
        if (j >= 0) next[j] += diff;
        prev = next;
      }
      apply(prev);
      window.renderDealsList();
    };
  }

  // ---------- 2/3. 计数与数据来源说明 ----------
  const _render = window.renderDealsList;
  window.renderDealsList = function () {
    _render();
    const badge = document.getElementById("dealCountBadge");
    if (!badge || typeof curatedDeals === "undefined") return;
    const auto = curatedDeals.length && curatedDeals[0].auto;
    const n = badge.textContent.match(/\d+/);
    badge.textContent = auto
      ? `共 ${n ? n[0] : curatedDeals.length} 个每日自动生成盘源（${curatedDeals[0].generated}）`
      : `共 ${n ? n[0] : curatedDeals.length} 个盘源 · ⚠ 静态样本（${loadInfo}）`;
  };

  const _fetch = window.fetchCloudData;
  if (typeof _fetch === "function") {
    window.fetchCloudData = async function (isManual) {
      let src = "./market_data.json";
      try { src = localStorage.getItem("hk_proppulse_cloud_url") || src; } catch (e) {}
      try {
        const r = await fetch(src + (src.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        const c = d.curated_deals || [];
        loadInfo = !c.length ? `${src} 里没有 curated_deals`
          : (c[0].auto ? "" : `${src} 生成于 ${d.updated_at || "?"}，仍是旧版数据：请在 Actions 以 normal 模式跑一次`);
      } catch (e) {
        loadInfo = `读取 ${src} 失败：${e.message}`;
      }
      await _fetch(isManual);
      window.renderDealsList();
    };
  }

  document.querySelectorAll(".section-title").forEach(t => {
    if (t.textContent.includes("多维权重筛选引擎")) t.textContent = "🎯 多维权重筛选引擎（盘源每日由实盘库自动生成 · 四维分数由数据计算）";
  });
  window.renderDealsList();
  if (typeof window.fetchCloudData === "function") window.fetchCloudData(false);
})();
