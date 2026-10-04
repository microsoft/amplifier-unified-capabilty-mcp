/** Private authenticated host proof and exact durable owner receipt, never a public action. */
export interface AdmissionAbortBinding {commandId:string;fenceId:string;instanceId:string;dataScope:string;}
export interface AdmissionAbortProof extends AdmissionAbortBinding {kind:'distribution-admission-abort';verified:true;purpose:'distribution-update';receiptId:string;}
export interface AdmissionAbortOwnerReceipt extends AdmissionAbortBinding {ownerId:string;status:'released'|'not-acquired';receiptId:string;}
const fields=['commandId','fenceId','instanceId','dataScope'] as const;
const token=(value:unknown)=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f]/.test(value);
export function admissionAbortProof(context:AdmissionAbortBinding&{purpose:string;serviceIdentity?:unknown},proof:AdmissionAbortProof){
 if(context.purpose!=='distribution-update'||context.serviceIdentity!==undefined||fields.some(key=>!token(context[key]))||!proof||Object.keys(proof).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,kind,purpose,receiptId,verified'||proof.kind!=='distribution-admission-abort'||proof.verified!==true||proof.purpose!=='distribution-update'||!token(proof.receiptId)||fields.some(key=>proof[key]!==context[key]))throw Error('Exact authenticated MCP admission abort proof required');
 return structuredClone(proof);
}
export function admissionAbortReceipt(ownerId:string,context:AdmissionAbortBinding,value:AdmissionAbortOwnerReceipt){
 if(!value||Object.keys(value).sort().join(',')!=='commandId,dataScope,fenceId,instanceId,ownerId,receiptId,status'||value.ownerId!==ownerId||!['released','not-acquired'].includes(value.status)||!token(value.receiptId)||fields.some(key=>value[key]!==context[key]))throw Error('Exact durable MCP admission abort receipt was not confirmed');
 return structuredClone(value);
}
