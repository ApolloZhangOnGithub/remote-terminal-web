// RTW touch scroll + mic button — injected into ttyd
(function(){
  function init(){
    const vp = document.querySelector(".xterm-viewport");
    if(!vp){ setTimeout(init, 300); return; }

    // Touch scroll
    let sy=0, scrolling=false;
    document.addEventListener("touchstart", e=>{
      if(e.touches.length!==1) return;
      sy=e.touches[0].clientY; scrolling=false;
    }, {capture:true, passive:true});
    document.addEventListener("touchmove", e=>{
      if(e.touches.length!==1) return;
      const cy=e.touches[0].clientY, dy=sy-cy;
      if(!scrolling && Math.abs(dy)>6) scrolling=true;
      if(scrolling){
        e.stopImmediatePropagation();
        e.preventDefault();
        vp.scrollTop += dy;
        sy=cy;
      }
    }, {capture:true, passive:false});
    document.addEventListener("touchend", e=>{
      if(scrolling){ e.stopImmediatePropagation(); e.preventDefault(); scrolling=false; }
    }, {capture:true, passive:false});

    // Toggle button for auxiliary keys
    const style = document.createElement("style");
    style.textContent = "#rtw-toggle{position:fixed;bottom:8px;right:8px;z-index:9999;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:50%;width:36px;height:36px;font-size:18px;display:flex;align-items:center;justify-content:center;cursor:pointer;touch-action:manipulation}#rtw-toggle.open{background:#ff8a65;color:#1a1208}#rtw-bar{display:none;position:fixed;bottom:50px;left:4px;right:4px;z-index:9998;flex-wrap:wrap;gap:4px;padding:6px;background:#161b22ee;border:1px solid #30363d;border-radius:8px;max-height:40vh;overflow-y:auto}#rtw-bar.show{display:flex}#rtw-bar button{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:8px 10px;font-size:13px;font-family:monospace;min-width:38px;touch-action:manipulation;cursor:pointer}";
    document.head.appendChild(style);

    const bar = document.createElement("div"); bar.id="rtw-bar";
    const tog = document.createElement("div"); tog.id="rtw-toggle"; tog.textContent="⌨";
    tog.onclick=()=>{ bar.classList.toggle("show"); tog.classList.toggle("open"); };

    const term = document.querySelector(".xterm");
    const KEYS = [
      ["Esc","\x1b"],["Tab","\t"],["^C","\x03"],["^D","\x04"],["^Z","\x1a"],
      ["Up","\x1b[A"],["Dn","\x1b[B"],["Lt","\x1b[D"],["Rt","\x1b[C"],
      ["C-b","\x02"],["-","-"],["_","_"],["/","/"],["?","?"],
      ["|","|"],["~","~"],["*","*"],["=","="],
      ["\"","\""],["'","'"],["$","$"],["!","!"],["&","&"],["<","<"],[">",">"]
    ];
    function sendKey(k){
      if(!term) return;
      const ev = new KeyboardEvent("keydown",{key:k,bubbles:true});
      // Use xterm textarea input
      const ta = document.querySelector(".xterm-helper-textarea");
      if(ta){
        const ie = new InputEvent("data",{data:k,inputType:"insertText",bubbles:true});
        // Direct write via ws
        const wsInput = document.querySelector("textarea.xterm-helper-textarea");
        if(wsInput){ wsInput.focus(); document.execCommand("insertText",false,k); }
      }
    }
    KEYS.forEach(([label,key])=>{
      const b=document.createElement("button"); b.textContent=label;
      b.onclick=()=>sendKey(key);
      bar.appendChild(b);
    });

    document.body.appendChild(bar);
    document.body.appendChild(tog);
    console.log("[RTW] touch scroll + toolbar injected");
  }
  if(document.readyState==="complete") init(); else window.addEventListener("load",init);
})();
