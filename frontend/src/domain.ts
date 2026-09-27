// Public demo mirrors the fixed policy for UX. The backend is the authority.
import policy from '../../policy/domain.json';
export const domainRefusal=policy.refusal;
export const domainClarification=policy.clarification;
const normalize=(s:string)=>s.normalize('NFKC').toLowerCase().replace(/\p{Cf}/gu,'').replace(/\s+/gu,' ').trim();
function matches(s:string,patterns:string[]){const n=normalize(s);return patterns.some(p=>new RegExp(p,'iu').test(n)||new RegExp(p,'iu').test(n.replaceAll(' ','')))}
const categories=(s:string)=>Object.entries(policy.categories).filter(([,terms])=>terms.some(t=>normalize(s).includes(t))).map(([k])=>k);
export function domainDecision(question:string){
  if(matches(question,policy.injection_patterns))return {status:'out_of_scope',question:'',mixed:false,help:''};
  const parts=question.split(/[?？。;；\n]+|(?:另外|顺便|同时|以及|并且|再帮我|然后|和(?=宇宙|黑洞|娱乐|天气|讲|写))/u).map(p=>p.trim()).filter(Boolean);
  const accepted:string[]=[];let blocked=false,uncertain=false,help='';
  for(const part of parts){if(matches(part,policy.outside_patterns)){blocked=true;continue}if(!categories(part).length){uncertain=true;continue}
    if(policy.system_patterns.some(t=>normalize(part).includes(t))){const topic=Object.entries(policy.help_topics).find(([,v])=>v.terms.some(t=>normalize(part).includes(t)));if(topic){if(accepted.length&&help!==topic[0])return {status:'clarify',question:'',mixed:false,help:''};help=topic[0]}}
    accepted.push(part);
  }
  if(!accepted.length||uncertain)return {status:!accepted.length&&blocked?'out_of_scope':'clarify',question:'',mixed:false,help:''};
  return {status:'allowed',question:accepted.join('？')+'？',mixed:blocked,help:help?policy.help_topics[help as keyof typeof policy.help_topics].answer:''};
}
export function safeDemoSource(question:string,text:string){
  return !matches(text,policy.injection_patterns)&&!matches(text,policy.outside_patterns)&&categories(text).some(k=>categories(question).includes(k));
}
