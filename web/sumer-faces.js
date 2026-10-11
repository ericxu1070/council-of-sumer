/* Small carved figures. Deliberately square: linen, clay, black hair, two eyes. */
(function () {
  "use strict";
  const esc = text => String(text ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const roles = {
    provider: { robe: "#b8a778", hat: "#ded0a4", beard: false },
    farmer: { robe: "#777a49", hat: "#aa8c47", beard: true },
    merchant: { robe: "#a5653c", hat: "#dcc293", beard: true },
    priest: { robe: "#d6c49b", hat: "#c3ac75", beard: false },
    villager: { robe: "#93714c", hat: "#796b49", beard: true },
  };
  function svg(advisor, options = {}) {
    const id = String(advisor.id || "unknown");
    let hash = 0; for (const c of id) hash = (hash * 31 + c.charCodeAt(0)) >>> 0;
    const role = roles[advisor.persona || id] || Object.values(roles)[hash % 5];
    const skin = ["#ba875a", "#c99867", "#a9774f"][hash % 3];
    const title = options.title === "" ? "" : `<title>${esc(options.title || advisor.short || id)}</title>`;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 48" shape-rendering="crispEdges" role="img">${title}
      <path d="M0 0h40v48H0z" fill="#b69a67"/>
      <path d="M2 2h36v44H2z" fill="#998054"/>
      <path d="M11 34h18v4h5v10H6V38h5z" fill="#30271c"/>
      <path d="M12 35h16v4h4v9H8v-9h4z" fill="${role.robe}"/>
      <path d="M17 31h6v6h-6z" fill="${skin}"/>
      <path d="M12 13h16v6h3v10h-3v4H12v-4H9V19h3z" fill="${skin}"/>
      <path d="M12 13h16v4H12zM9 17h3v9H9zM28 17h3v9h-3z" fill="#35291e"/>
      <path d="M10 10h20v7H10zM7 17h26v3H7z" fill="${role.hat}"/>
      <path d="M10 14h20v2H10z" fill="#5a4930"/>
      <path d="M14 23h3v3h-3zM23 23h3v3h-3z" fill="#292117"/>
      <path d="M19 26h2v3h-2z" fill="#996741"/>
      ${role.beard ? '<path d="M12 28h4v3h8v-3h4v7h-3v3H15v-3h-3z" fill="#392b20"/><path d="M16 32v3m4-3v4m4-4v3" stroke="#74553b"/>' : '<path d="M17 31h6v2h-6z" fill="#75462d"/>'}
      <path d="M11 40h3v8h-3M27 40h2v8h-2" fill="#34291c" opacity=".3"/>
    </svg>`;
  }
  window.Faces = { svg, PERSONAS: Object.keys(roles) };
})();
