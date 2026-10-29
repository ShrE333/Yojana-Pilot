"""Server-only Sarvam REST integration. No audio or provider payload is logged/stored."""
import base64
import io
import os
import wave
import json
import httpx
from fastapi import HTTPException
from core.languages import normalize, provider_code, TTS

MAX_AUDIO = 512000


def request(path, **kwargs):
    key = os.getenv('SARVAM_API_KEY')
    if not key:
        raise HTTPException(503, {'code': 'speech_not_configured'})
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
            with client.stream('POST', 'https://api.sarvam.ai/' + path,
                               headers={'api-subscription-key': key}, **kwargs) as response:
                if response.status_code == 429:
                    raise HTTPException(429, {'code': 'speech_rate_limited'})
                if response.status_code != 200:
                    raise HTTPException(502, {'code': 'speech_provider_unavailable'})
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 2000000:
                        raise HTTPException(502, {'code': 'speech_response_too_large'})
                return json.loads(content)
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, {'code': 'speech_provider_unavailable'})


def tts(text, language):
    if normalize(language) not in TTS:
        raise HTTPException(422, {'code': 'tts_language_unavailable', 'fallback': 'read_text'})
    data = request('text-to-speech', json={'text': text, 'language_code': provider_code(language),
                   'model': os.getenv('SARVAM_TTS_MODEL', 'bulbul:v3'), 'output_audio_codec': 'wav'})
    try:
        audio = base64.b64decode(data['audios'][0], validate=True)
        if len(audio) > 1500000 or not audio.startswith(b'RIFF'):
            raise ValueError()
        return audio
    except (KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(502, {'code': 'invalid_speech_response'})


def stt(encoded, language):
    if os.getenv('SARVAM_STT_ENABLED', 'false').lower() != 'true':
        raise HTTPException(503, {'code': 'stt_not_enabled', 'fallback': 'type'})
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AUDIO:
            raise ValueError()
        with wave.open(io.BytesIO(raw)) as wav:
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000
                    or wav.getnframes() > 15 * 16000 or wav.getnframes() == 0):
                raise ValueError()
            if len(wav.readframes(wav.getnframes())) != wav.getnframes()*2:
                raise ValueError()
    except (ValueError, wave.Error, EOFError):
        raise HTTPException(422, {'code': 'audio_requires_mono_16khz_pcm_wav_under_15_seconds'})
    data = request('speech-to-text', files={'file': ('speech.wav', raw, 'audio/wav')},
                   data={'model': os.getenv('SARVAM_STT_MODEL', 'saaras:v3'), 'mode': 'transcribe',
                         'language_code': provider_code(language)})
    text = data.get('transcript')
    if not isinstance(text, str) or not text.strip() or len(text) > 1800:
        raise HTTPException(502, {'code': 'invalid_transcript'})
    from .conversation_api import _safe_message
    return {'text': _safe_message(text), 'language': normalize(data.get('language_code'))}
