/** Original synthesized candy-like sounds; no recordings or game assets. */
export type Severity = 'info' | 'warning' | 'critical';
export type SoundSuggestion = {id:string; severity:string; summary:string;recommended_action?:unknown;expected_result?:string|null};
export const melodies: Record<Severity, {note:number;at:number;length:number}[]> = {
  info: [ {note:1046.5,at:0,length:.17}, {note:1568,at:.11,length:.29}, {note:2093,at:.22,length:.32} ],
  warning: [ {note:784,at:0,length:.16}, {note:1046.5,at:.12,length:.22}, {note:784,at:.33,length:.16}, {note:1174.7,at:.45,length:.3} ],
  critical: [ {note:659.3,at:0,length:.13}, {note:880,at:.13,length:.14}, {note:659.3,at:.3,length:.13}, {note:880,at:.43,length:.14}, {note:1318.5,at:.61,length:.32} ],
};
export function signature(s:SoundSuggestion){return JSON.stringify([s.id,s.severity,s.summary,s.recommended_action??null,s.expected_result??null])}
export function newSuggestionSounds(previous:SoundSuggestion[], next:SoundSuggestion[]):Severity[]{
  const seen=new Set(previous.map(signature));
  return next.filter(s=>!seen.has(signature(s))&&Object.hasOwn(melodies,s.severity)).map(s=>s.severity as Severity);
}

export class SuggestionAudio {
  private context:AudioContext|null=null;
  private master:GainNode|null=null;
  private end=0;
  private nodes=new Set<OscillatorNode>();
  async unlock(volume:number){
    if(!this.context){
      this.context=new AudioContext();
      this.master=this.context.createGain();
      this.master.connect(this.context.destination);
    }
    this.volume(volume);
    if(this.context.state==='suspended')await this.context.resume();
    if(this.context.state!=='running')throw Error('Audio could not be enabled');
  }
  volume(value:number){
    if(this.context&&this.master)this.master.gain.setTargetAtTime(Math.max(0,Math.min(1,value))*.22,this.context.currentTime,.02);
  }
  play(level:Severity){
    const context=this.context, master=this.master;
    if(!context||!master||context.state!=='running')return;
    const start=Math.max(context.currentTime+.025,this.end);
    for(const {note,at,length} of melodies[level]){
      // A soft fundamental with a quiet, slightly detuned bell overtone.
      for(const [multiple,amplitude] of [[1,.6],[2.005,.1]]){
        const oscillator=context.createOscillator(), envelope=context.createGain();
        oscillator.type='sine';
        oscillator.frequency.setValueAtTime(note*multiple,start+at);
        oscillator.frequency.exponentialRampToValueAtTime(note*multiple*.995,start+at+length);
        envelope.gain.setValueAtTime(0,start+at);
        envelope.gain.linearRampToValueAtTime(amplitude,start+at+.009);
        envelope.gain.exponentialRampToValueAtTime(.0001,start+at+length);
        oscillator.connect(envelope);envelope.connect(master);
        oscillator.start(start+at);oscillator.stop(start+at+length+.025);
        this.nodes.add(oscillator);
        oscillator.onended=()=>{this.nodes.delete(oscillator);oscillator.disconnect();envelope.disconnect()};
      }
    }
    this.end=start+1.1; // Never stack simultaneous card arrivals into a noisy chord.
  }
  stop(){
    for(const node of this.nodes){try{node.stop()}catch{/* Already ended. */}}
    this.nodes.clear();this.end=0;
  }
  close(){this.stop();void this.context?.close();this.context=null;this.master=null}
}
