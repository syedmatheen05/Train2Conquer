document.addEventListener("DOMContentLoaded",()=>{
 const toggle=document.getElementById("t2cToggle"),links=document.getElementById("t2cLinks");
 if(toggle&&links){toggle.addEventListener("click",()=>{const open=links.classList.toggle("open");toggle.classList.toggle("open",open);toggle.setAttribute("aria-expanded",String(open));});links.querySelectorAll("a").forEach(a=>a.addEventListener("click",()=>{links.classList.remove("open");toggle.classList.remove("open");toggle.setAttribute("aria-expanded","false");}));}
 const trigger=document.getElementById("t2cProfileTrigger"),dropdown=document.getElementById("t2cDropdown");
 if(trigger&&dropdown){const close=()=>{dropdown.classList.remove("open");trigger.setAttribute("aria-expanded","false")};trigger.addEventListener("click",e=>{e.stopPropagation();const open=!dropdown.classList.contains("open");dropdown.classList.toggle("open",open);trigger.setAttribute("aria-expanded",String(open));});document.addEventListener("click",e=>{if(!dropdown.contains(e.target)&&!trigger.contains(e.target))close()});document.addEventListener("keydown",e=>{if(e.key==="Escape")close()});}
});
