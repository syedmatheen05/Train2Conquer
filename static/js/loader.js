(()=>{
 "use strict";
 const hide=()=>{const l=document.getElementById("page-loader");document.documentElement.classList.remove("t2c-loading");document.body?.classList.remove("t2c-loading");if(l)l.classList.add("hide")};
 const show=(msg="Loading...")=>{const l=document.getElementById("page-loader");if(!l)return;const t=document.getElementById("loader-message");if(t)t.textContent=msg;document.documentElement.classList.add("t2c-loading");document.body?.classList.add("t2c-loading");l.classList.remove("hide")};
 const local=url=>{try{const u=new URL(url,location.href);return u.origin===location.origin&&(u.protocol==="http:"||u.protocol==="https:")}catch{return false}};
 document.addEventListener("DOMContentLoaded",()=>{if(!document.getElementById("page-loader"))return;const finish=()=>setTimeout(hide,80);if(document.readyState==="complete")finish();else addEventListener("load",finish,{once:true});
  document.addEventListener("click",e=>{const a=e.target.closest("a");if(!a||e.defaultPrevented||a.dataset.noLoader!==undefined||a.target==="_blank"||a.hasAttribute("download"))return;const href=a.getAttribute("href");if(!href||href.startsWith("#")||href.startsWith("mailto:")||href.startsWith("tel:")||href.startsWith("javascript:"))return;if(!local(a.href))return;const u=new URL(a.href),c=new URL(location.href);if(u.href!==c.href)show(a.dataset.loaderMessage||"Loading...")});
  document.addEventListener("submit",e=>{const f=e.target;if(!(f instanceof HTMLFormElement)||e.defaultPrevented||f.dataset.noLoader!==undefined||f.target==="_blank")return;const action=f.getAttribute("action")||location.href;if(local(action))show(f.dataset.loaderMessage||"Processing...")});
  addEventListener("pageshow",hide);
 });
})();
