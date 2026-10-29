'use strict';
(() => {
 const $=id=>document.getElementById(id), create=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e};
 let handoff=new URLSearchParams(location.hash.slice(1)).get('handoff');
 if(location.hash)history.replaceState(null,'',location.pathname); // Never retain a capability in history/referrers.
 let cid=null,sid=null,busy=false,muted=false,ended=false,recognition=null,recorder=null,stream=null,audio=null,audioURL=null,recordTimer=null,lastRetry=null,capabilities={},pollTimer=null;
 let speechEpoch=0,speechAbort=null,serverSpeechDisabled=false,creditsExhausted=false;
 const unsupportedSpeech=new Set();
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 const language=()=>$('voice-language').value;
 function state(name,help){document.body.dataset.state=name.toLowerCase();$('voice-state').textContent=name;if(help)$('voice-help').textContent=help}
 function retry(fn){lastRetry=fn;$('voice-retry').disabled=!fn}
 async function api(path,body,method='POST'){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),70000);
  try{const r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
   const data=await r.json().catch(()=>({}));if(!r.ok){const error=new Error(typeof data.detail==='string'?data.detail:data.detail?.code?.replaceAll('_',' ')||'Request failed. Please retry.');error.status=r.status;throw error}return data
  }finally{clearTimeout(timer)}
 }
 function controls(){for(const id of ['voice-listen','voice-send'])$(id).disabled=!cid||busy||ended;$('voice-end').disabled=!cid||ended;$('voice-mute').disabled=!cid}
 function append(role,text){$('voice-transcript').append(create('div',text,'turn '+role));$('voice-transcript').scrollTop=$('voice-transcript').scrollHeight}
 function cancelPlayback(){speechEpoch++;speechAbort?.abort();speechAbort=null;if(audio){audio.onended=null;audio.pause();audio=null}if(audioURL){URL.revokeObjectURL(audioURL);audioURL=null}window.speechSynthesis?.cancel()}
 function stopMedia(){recognition?.abort();recognition=null;if(recorder?.state==='recording')recorder.stop();recorder=null;stream?.getTracks().forEach(t=>t.stop());stream=null;clearTimeout(recordTimer);cancelPlayback()}
 async function start(){
  stopMedia();serverSpeechDisabled=creditsExhausted;unsupportedSpeech.clear();$('summary-consent').checked=false;
  $('voice-start').disabled=true;state('Connecting','Opening your authorized case…');
  try{
   if(handoff){const linked=await api('/api/voice/redeem',{token:handoff});sid=linked.session_id;handoff=null}
   else{try{sid=sessionStorage.getItem('yojana-case-id')}catch{}}
   const data=await api('/api/voice/start',{session_id:sid});cid=data.call_id;sid=data.session_id;ended=false;
   try{sessionStorage.setItem('yojana-case-id',sid)}catch{}
   const history=await api('/api/voice/'+cid,undefined,'GET');$('voice-transcript').replaceChildren();for(const m of history.transcript)append(m.role,m.text);
   controls();retry(null);state('Ready',Recognition?'Choose Listen, or type. Your microphone stays off until you choose Listen.':capabilities.stt_configured?'Browser recognition is unavailable. Listen records up to 15 seconds for server transcription, or type.':'Speech recognition is unavailable in this browser. Type below to use the same session.');
  }catch(e){state('Unable to connect',e.message);$('voice-start').disabled=false;if(e.status===403||e.status===404){try{sessionStorage.removeItem('yojana-case-id')}catch{}sid=null}}
 }
 async function speak(result,final=false){
  cancelPlayback();const epoch=speechEpoch;
  const current=()=>epoch===speechEpoch&&!muted&&(!ended||final);
  if(!current())return;
  const text=(result.spoken_text||result.message).slice(0,360),lang=result.text_fallback?'en-IN':(result.language||'en')+'-IN';
  const done=help=>{if(current())state(final?'Session ended':'Ready',help)};
  function device(){if(!current())return;
   if(window.speechSynthesis&&window.SpeechSynthesisUtterance){const utterance=new SpeechSynthesisUtterance(text);utterance.lang=lang;
    utterance.onend=()=>done('Device speech finished. Your text and saved results remain available.');
    utterance.onerror=()=>done('Device speech is unavailable. Continue using the readable transcript.');
    state(final?'Session ended':'Speaking','Using available device voice. Server speech may be unavailable.');window.speechSynthesis.speak(utterance);
   }else done('Audio is unavailable. Continue using the readable transcript.');
  }
  if(!capabilities.tts_configured||serverSpeechDisabled||unsupportedSpeech.has(lang)){device();return}
  state(final?'Session ended':'Speaking','Your reply is ready. Preparing a short spoken response…');
  speechAbort=new AbortController();const controller=speechAbort,timer=setTimeout(()=>controller.abort(),12000);
  try{const response=await fetch('/api/voice/tts',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({call_id:cid,text,lang,request_id:result.request_id}),signal:controller.signal});
   if(!current())return;
   if(!response.ok){const error=await response.json().catch(()=>({}));
    if(response.status===409)return; // A newer reply superseded this audio.
    if(response.status===402){creditsExhausted=true;serverSpeechDisabled=true}
    else if(response.status===422)unsupportedSpeech.add(lang);else serverSpeechDisabled=true;
    $('voice-help').textContent=error.detail?.code?.replaceAll('_',' ')||'Server speech unavailable; using device voice or text.';device();return
   }
   const blob=await response.blob();if(!current())return;
   audioURL=URL.createObjectURL(blob);audio=new Audio(audioURL);
   audio.onended=()=>{if(audioURL)URL.revokeObjectURL(audioURL);audioURL=null;done('Choose Listen or type to continue, or review your final results.')};
   audio.onerror=()=>{if(current()){serverSpeechDisabled=true;cancelPlayback();void speak({...result},final)}};
   await audio.play();retry(()=>speak(result,final));
  }catch{if(current()){serverSpeechDisabled=true;device()}}finally{clearTimeout(timer)}
 }
 async function send(text,requestId=crypto.randomUUID()){
  if(busy||!cid||ended||!text.trim())return;cancelPlayback();busy=true;recognition?.stop();controls();state('Thinking','Reading your words and checking the scheme rules…');
  try{const data=await api('/api/voice/'+cid+'/turn',{text,lang:language(),language_override:language()!=='auto',request_id:requestId});
   if(ended)return;
   append('user',text);append('assistant',data.message+(data.question?.reason?'\nWhy I ask: '+data.question.reason:''));$('voice-text').value='';$('live-transcript').textContent='';retry(null);
   data.request_id=requestId;
   if(data.assessment_complete){ended=true;stopMedia();renderSummary(data.summary||{schemes:data.recommendations,caveat:'Guidance only; verify official sources and missing conditions.'});delivery({whatsapp:{status:'not_requested'}});controls();state('Session ended','Assessment complete. Your results are saved; you can refine them in the dashboard.');$('voice-start').disabled=false;$('voice-start').textContent='Start another session';
    if($('summary-consent').checked)api('/api/voice/'+cid+'/end',{consent_whatsapp:true}).then(delivery).catch(e=>{$('summary-status').textContent='Summary saved, but WhatsApp send was not requested successfully. '+e.message});
    void speak(data,true);
   }else void speak(data)
  }catch(e){if(!ended){state('Message not completed',e.name==='AbortError'?'The request timed out. Retry safely or type again.':e.message);$('voice-text').value=text;retry(()=>send(text,requestId))}}
  finally{busy=false;controls()}
 }
 function wav(buffer){
  const length=Math.min(Math.floor(buffer.duration*16000),240000), data=new ArrayBuffer(44+length*2),view=new DataView(data);
  const word=(offset,text)=>{for(let i=0;i<text.length;i++)view.setUint8(offset+i,text.charCodeAt(i))};
  word(0,'RIFF');view.setUint32(4,36+length*2,true);word(8,'WAVE');word(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,'data');view.setUint32(40,length*2,true);
  for(let i=0;i<length;i++){let sample=0;for(let c=0;c<buffer.numberOfChannels;c++)sample+=buffer.getChannelData(c)[Math.min(buffer.length-1,Math.floor(i*buffer.sampleRate/16000))]/buffer.numberOfChannels;sample=Math.max(-1,Math.min(1,sample));view.setInt16(44+2*i,sample<0?sample*32768:sample*32767,true)}
  return new Uint8Array(data)
 }
 async function serverRecord(){
  state('Microphone permission','Allow the microphone to record up to 15 seconds, or use typed input.');
  try{
   stream=await navigator.mediaDevices.getUserMedia({audio:true});const chunks=[];recorder=new MediaRecorder(stream);
   recorder.ondataavailable=e=>chunks.push(e.data);recorder.onstop=async()=>{
    stream?.getTracks().forEach(t=>t.stop());stream=null;recorder=null;$('voice-listen').textContent='Listen';if(ended||muted)return;
    state('Transcribing','Sending this short audio clip to the configured speech service…');busy=true;controls();
    try{const context=new (window.AudioContext||window.webkitAudioContext)();let decoded;try{decoded=await context.decodeAudioData(await new Blob(chunks).arrayBuffer())}finally{await context.close()}
     const bytes=wav(decoded);let binary='';for(const b of bytes)binary+=String.fromCharCode(b);
     const data=await api('/api/voice/stt',{call_id:cid,audio:btoa(binary),lang:language()});busy=false;controls();if(ended)return;$('voice-text').value=data.text;if(!muted)await send(data.text)
    }catch(e){if(!ended){state('Transcription unavailable','Please type your message. '+e.message);retry(listen)}}finally{busy=false;controls()}
   };
   recorder.start();$('voice-listen').textContent='Stop recording';state('Listening','Recording up to 15 seconds. Tap Stop recording when finished.');recordTimer=setTimeout(()=>recorder?.stop(),15000);
  }catch(e){stream?.getTracks().forEach(t=>t.stop());stream=null;state('Microphone unavailable','Permission was denied or recording is unsupported. You can type instead.');retry(listen)}
 }
 function listen(){
  if(!cid||busy||muted||ended)return;if(recorder?.state==='recording'){clearTimeout(recordTimer);recorder.stop();return}
  stopMedia();
  if(!Recognition){if(capabilities.stt_configured&&navigator.mediaDevices&&window.MediaRecorder)return serverRecord();state('Type your message','Speech recognition is not available here. The typed session has the same reasoning and history.');$('voice-text').focus();return}
  recognition=new Recognition();recognition.lang=language()==='auto'?(navigator.language||'en-IN'):language();recognition.interimResults=true;recognition.continuous=false;
  recognition.onstart=()=>state('Listening','Speak naturally. You can mute or type at any time.');
  recognition.onresult=e=>{const text=Array.from(e.results).map(r=>r[0].transcript).join(' ');$('live-transcript').textContent=text;$('voice-text').value=text;
   if(e.results[e.results.length-1].isFinal){state('Transcribing','Using the recognized text…');send(text)}};
  recognition.onerror=e=>{if(muted||ended||e.error==='aborted')return;state('Recognition unavailable','Microphone permission or language support failed. Type below or retry.');retry(listen)};
  recognition.onend=()=>{if(!busy&&!ended&&!muted)state('Ready','Choose Listen to speak again or use typed input.')};
  try{state('Microphone permission','Waiting for browser microphone access…');recognition.start()}catch{state('Recognition unavailable','Please type or try Listen again.');retry(listen)}
 }
 function renderSummary(snapshot){const dest=$('voice-summary');dest.replaceChildren(create('h2','Your ranked shortlist and next steps'));
  if(!snapshot.schemes.length)dest.append(create('p','There is not enough verified information to recommend a scheme yet. Continue your case from the dashboard.'));
  snapshot.schemes.forEach((item,i)=>{const card=create('article','','summary-card');card.append(create('h3',`${i+1}. ${item.name}`),create('p',item.benefit||'Benefit details need official verification.'),create('p',item.why||item.status));
   card.append(create('p','Missing: '+(item.missing_requirements?.map(f=>f.replaceAll('_',' ')).join(', ')||'Review any manual conditions at the source.')));
   for(const step of item.next_steps||[])card.append(create('p','Next: '+step));
   for(const condition of item.check_yourself||[])card.append(create('p','Check manually: '+condition));
   try{const url=new URL(item.source_url);if(url.protocol==='https:'){const a=create('a','Read the source and verify requirements ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';card.append(a)}}catch{}
   dest.append(card)
  });dest.append(create('p',snapshot.caveat));
 }
 function delivery(data){const status=data.whatsapp.status;const labels={not_requested:'No WhatsApp summary requested.',pending:'Summary queued; not yet accepted by WAHA.',retrying:'Send failed; retry is scheduled. Delivery is not confirmed.',failed:'Summary send failed. Use Retry summary to request another attempt.',cancelled:'Summary cancelled. No new send will be attempted without renewed consent.',accepted_by_waha:'WAHA accepted the summary. Recipient delivery has not been verified.'};$('summary-status').textContent=labels[status]||'Summary status unavailable.';$('summary-refresh').hidden=false;$('summary-refresh').textContent=['not_requested','cancelled'].includes(status)?'Send summary with consent':status==='failed'?'Retry summary':'Check WhatsApp status';$('summary-refresh').dataset.status=status;$('summary-cancel').hidden=!['pending','retrying','failed'].includes(status)}
 async function refresh(){try{let data;const status=$('summary-refresh').dataset.status;
  if(['not_requested','cancelled'].includes(status)){if(!$('summary-consent').checked)throw new Error('Check the consent box before requesting a WhatsApp summary.');data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:true})}
  else{if(status==='failed')await api('/api/voice/'+cid+'/summary/retry',{});data=await api('/api/voice/'+cid,undefined,'GET')}delivery(data)
 }catch(e){$('summary-status').textContent='Summary not confirmed. '+e.message}}
 async function end(){if(!cid||ended)return;ended=true;stopMedia();controls();state('Preparing summary','Finishing any in-flight turn, saving your minimized transcript and ranked next steps…');
  try{const data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:$('summary-consent').checked});renderSummary(data.summary);delivery(data);state('Session ended','Your saved case remains available in the dashboard.');$('voice-start').disabled=false;$('voice-start').textContent='Start another session';retry(null)}
  catch(e){ended=false;controls();state('Summary not saved',e.message);retry(end)}
 }
 $('voice-start').onclick=start;$('voice-listen').onclick=listen;$('voice-end').onclick=end;$('voice-retry').onclick=()=>lastRetry?.();$('summary-refresh').onclick=refresh;
 $('summary-cancel').onclick=async()=>{try{await api('/api/voice/'+cid+'/summary/cancel',{});$('summary-consent').checked=false;delivery(await api('/api/voice/'+cid,undefined,'GET'))}catch(e){$('summary-status').textContent=e.message}};
 $('voice-mute').onclick=()=>{muted=!muted;$('voice-mute').setAttribute('aria-pressed',String(muted));$('voice-mute').textContent=muted?'Unmute':'Mute';if(muted)stopMedia();state(muted?'Muted':'Ready',muted?'Microphone and playback are off. Typed input still works.':'Choose Listen to speak.')};
 $('voice-form').onsubmit=e=>{e.preventDefault();send($('voice-text').value)};
 fetch('/api/voice/capabilities').then(r=>r.json()).then(data=>{capabilities=data;for(const lang of data.languages){const o=create('option',lang.name+' · '+lang.code);o.value=lang.code;$('voice-language').append(o)}}).catch(()=>{$('coverage').textContent='Language coverage could not be loaded; typed input remains available.'});
 $('voice-language').onchange=()=>{const lang=capabilities.languages?.find(l=>l.code===language());$('coverage').textContent=lang?`Text: ${lang.text}; browser recognition: device dependent; Sarvam TTS: ${lang.sarvam_tts}. Live coverage is unverified.`:'Automatic text detection; browser listening follows the device language. Choose a language for more reliable recognition.'};
 window.addEventListener('pagehide',()=>{ended=true;stopMedia();clearInterval(pollTimer)});
 window.addEventListener('pageshow',event=>{if(event.persisted){cid=null;ended=false;busy=false;controls();$('voice-start').disabled=false;$('voice-start').textContent='Resume saved session';state('Ready','Your accepted turns are saved. Resume to continue.')}});
})();
