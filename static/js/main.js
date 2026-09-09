document.addEventListener("DOMContentLoaded",()=>{
 const toggle=document.getElementById("t2cToggle"),links=document.getElementById("t2cLinks");
 if(toggle&&links){toggle.addEventListener("click",()=>{const open=links.classList.toggle("open");toggle.classList.toggle("open",open);toggle.setAttribute("aria-expanded",String(open));});links.querySelectorAll("a").forEach(a=>a.addEventListener("click",()=>{links.classList.remove("open");toggle.classList.remove("open");toggle.setAttribute("aria-expanded","false");}));}
 const trigger=document.getElementById("t2cProfileTrigger"),dropdown=document.getElementById("t2cDropdown");
 if(trigger&&dropdown){const close=()=>{dropdown.classList.remove("open");trigger.setAttribute("aria-expanded","false")};trigger.addEventListener("click",e=>{e.stopPropagation();const open=!dropdown.classList.contains("open");dropdown.classList.toggle("open",open);trigger.setAttribute("aria-expanded",String(open));});document.addEventListener("click",e=>{if(!dropdown.contains(e.target)&&!trigger.contains(e.target))close()});document.addEventListener("keydown",e=>{if(e.key==="Escape")close()});}
 // Home link: if we're already on the Home page, don't reload the whole
 // page -- just scroll smoothly back to the top. Otherwise let it navigate
 // there as normal.
 const homeLink=document.getElementById("home");
 if(homeLink){homeLink.addEventListener("click",e=>{
  const linkUrl=new URL(homeLink.href,location.href);
  if(linkUrl.pathname===location.pathname){
   e.preventDefault();
   window.scrollTo({top:0,behavior:"smooth"});
  }
 });}
});
