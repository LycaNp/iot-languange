import json
import requests
import threading
import time
import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech import SpeechConfig, SpeechSynthesizer
from azure.cognitiveservices.speech.audio import AudioOutputConfig
from azure.iot.device import IoTHubDeviceClient, MethodResponse
from azure.iot.hub import IoTHubRegistryManager

# === Configuration ===
SPEECH_API_KEY = "6Vq7DyZBWHHi55JuCauGmMsYPtmNfvltWOlxYzka8t2voplXWLxFJQQJ99BEACLArgHXJ3w3AAAYACOGlJ11"
SPEECH_REGION = "southcentralus"

TRANSLATOR_API_KEY = "8MVkpdigBOlGMCAGtevkkG90EUZcRVeY5zq1XVpryhJGctOHoYyaJQQJ99BEACLArgHXJ3w3AAAbACOGZCPB"
TRANSLATOR_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
TRANSLATOR_REGION = "southcentralus"

FROM_LANGUAGE = "en-GB"
TO_LANGUAGE = "fr-FR"

DEVICE_CONNECTION_STRING = "HostName=smarttimerhub.azure-devices.net;DeviceId=SmartTimerDevice;SharedAccessKey=nBel3CUqfp8cf32l701FwBrk3HdgLScNmXnmog6fIHU="
IOTHUB_CONNECTION_STRING = "HostName=smarttimerhub.azure-devices.net;SharedAccessKeyName=iothubowner;SharedAccessKey=G7bZOTLnqTnC/Dz554bmvnMWCXyKfDNEMAIoTMx6Nok="
TARGET_DEVICE_ID = "LASasuss"
AZURE_FUNCTION_URL = "http://localhost:7071/api/text2timer"

# === Global States ===
listening = True
listening_lock = threading.Lock()

# === IoT Hub Device Client ===
print("Connecting to IoT Hub as device...")
device_client = IoTHubDeviceClient.create_from_connection_string(DEVICE_CONNECTION_STRING)
device_client.connect()
print("Device client connected.")

# === Speech Configuration ===
speech_config = SpeechConfig(subscription=SPEECH_API_KEY, region=SPEECH_REGION)
speech_config.speech_recognition_language = FROM_LANGUAGE
audio_config = AudioOutputConfig(use_default_speaker=True)

# === Voice Setup ===
temp_synth = speechsdk.SpeechSynthesizer(speech_config=speech_config)
voices = temp_synth.get_voices_async().get().voices
voice_short_name = next((v.short_name for v in voices if v.locale.lower() == FROM_LANGUAGE.lower()), None)

if voice_short_name:
    speech_config.speech_synthesis_voice_name = voice_short_name
    print(f"Using voice: {voice_short_name}")
else:
    print("No matching voice found.")
    voice_short_name = ""

synthesizer = SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

# === Functions ===

def send_to_device(text, lang=FROM_LANGUAGE):
    try:
        payload = {'speech': text, 'language': lang}
        payload_str = json.dumps(payload)
        print(f"Sending payload to {TARGET_DEVICE_ID}: {payload_str}")
        registry_manager = IoTHubRegistryManager(IOTHUB_CONNECTION_STRING)
        registry_manager.send_c2d_message(TARGET_DEVICE_ID, payload_str)
        print(f"Message sent to {TARGET_DEVICE_ID}")
    except Exception as e:
        print(f"Failed to send message: {e}")

def translate_text(text):
    try:
        url = f"{TRANSLATOR_ENDPOINT}/translate?api-version=3.0"
        headers = {
            "Ocp-Apim-Subscription-Key": TRANSLATOR_API_KEY,
            "Ocp-Apim-Subscription-Region": TRANSLATOR_REGION,
            "Content-Type": "application/json"
        }
        params = {"from": FROM_LANGUAGE, "to": TO_LANGUAGE}
        body = [{"text": text}]
        response = requests.post(url, headers=headers, params=params, json=body, timeout=5)
        response.raise_for_status()
        return response.json()[0]["translations"][0]["text"]
    except Exception as e:
        print(f"Translation error: {e}")
        return text

def say(text_en):
    if not voice_short_name:
        print("No valid voice to speak.")
        return
    if not text_en.strip():  # Skip empty strings
        print("Empty string provided to speak(), skipping.")
        return
    try:
        print(f"Speaking: {text_en}")
        recognizer.stop_continuous_recognition_async().get()
        time.sleep(0.3)
        ssml = f"<speak version='1.0' xml:lang='{FROM_LANGUAGE}'><voice xml:lang='{FROM_LANGUAGE}' name='{voice_short_name}'>{text_en}</voice></speak>"
        synthesizer.speak_ssml_async(ssml).get()
        time.sleep(0.5)
        recognizer.start_continuous_recognition_async().get()
    except Exception as e:
        print(f"Speech synthesis error: {e}")

def get_timer_time(text):
    if not any(k in text.lower() for k in ['second', 'seconds', 'minute', 'minutes', 'hour', 'hours']):
        print("No timer keywords found.")
        return 0
    try:
        response = requests.post(AZURE_FUNCTION_URL, json={"name": text}, timeout=5)
        response.raise_for_status()
        return response.json().get("seconds", 0)
    except Exception as e:
        print(f"Timer parsing error: {e}")
        return 0

def announce_timer(minutes, seconds):
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    say(f"Time's up on your {' '.join(parts)} timer.")

def create_timer(duration_seconds):
    minutes, seconds = divmod(duration_seconds, 60)
    threading.Timer(duration_seconds, announce_timer, args=[minutes, seconds]).start()
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    say(f"Timer started for {' '.join(parts)}")

def process_text(text):
    if not text.strip():  # Ignore blank recognized speech
        print("Empty recognized speech, skipping.")
        return
    with listening_lock:
        if not listening:
            return
    print(f"🗣 Recognized: {text}")
    translated = translate_text(text)
    send_to_device(translated, lang=FROM_LANGUAGE)
    say(text)
    seconds = get_timer_time(text)
    if seconds > 0:
        create_timer(seconds)

def handle_method_request(request):
    print(f"Method request received: {request.name}")
    if request.name == "set-timer":
        try:
            payload = json.loads(request.payload)
            seconds = payload.get("seconds", 0)
            if seconds > 0:
                create_timer(seconds)
        except Exception as e:
            print(f"Method error: {e}")
    response = MethodResponse.create_from_method_request(request, 200)
    device_client.send_method_response(response)

# === Set up recognizer ===
recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config)
recognizer.recognized.connect(
    lambda evt: process_text(evt.result.text)
    if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech else None
)

device_client.on_method_request_received = handle_method_request

# === Start Listening ===
recognizer.start_continuous_recognition()
print("🎤 Listening... Speak a command in English.")

# === Main Loop ===
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping...")
    recognizer.stop_continuous_recognition()
    device_client.disconnect()
    print("Device shut down.")
