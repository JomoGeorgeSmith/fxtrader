type SelectedAction={action_id?:string|null;recommended_action?:unknown;next_move?:string|null};

export const failedPlanNextMove='Do not execute this tested plan: it is not verified as a safe action. Ask Treasury to investigate another funding source or resolve the failed cash or exposure checks before reconsidering the payment.';

export function safeNextMove(card:SelectedAction){
 return card.action_id&&!card.recommended_action?failedPlanNextMove:card.next_move??null;
}
