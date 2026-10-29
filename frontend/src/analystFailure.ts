export type FailedAnalysis={suggestion_origin?:string;analyst_error?:string;analyst_audit?:{validation_attempts?:{error?:string}[]}};

export function failureExplanation(analysis:FailedAnalysis):string {
 if(analysis.suggestion_origin!=='unavailable')return '';
 const error=analysis.analyst_audit?.validation_attempts?.[0]?.error||'';
 if(error.startsWith('Unknown fact reference:'))return 'The analyst cited a calculation section instead of a specific supporting value. That citation could not be verified, so this run published no recommendations.';
 if(error)return 'The analyst response did not pass evidence or format checks, so this run published no recommendations.';
 return 'The analyst could not publish a verified response for this run. No recommendations were substituted.';
}
