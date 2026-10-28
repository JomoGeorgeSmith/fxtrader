type ReviewableAnalysis = {analysis_review_warnings?:string[];validation_warnings?:string[]};
type ReviewableInsight = {review_warnings?:string[]};

export function reviewNotes(analysis:ReviewableAnalysis, insight?:ReviewableInsight){
 const legacy=analysis.analysis_review_warnings===undefined;
 return {
  insight:insight?.review_warnings??[],
  snapshot:legacy?(analysis.validation_warnings??[]):analysis.analysis_review_warnings!,
  legacy,
 };
}
