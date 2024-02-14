from faster_whisper import WhisperModel
import numpy as np
import webrtcvad
import pyaudio
import threading
import time
import os

os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"

class VoiceRecorder:
    def __init__(self) -> None:
        # Set up WebRTC VAD
        self.webrtc_vad_model = webrtcvad.Vad()
        self.webrtc_vad_model.set_mode(1)  # set aggressiveness mode, in [0, 3]
        self.CHUNK = 1024 * 3
        self.RATE = 16000
        self.model_size = 'base.en'
        self.on_recording = False
        # self.frames_offset = 0.0
        # self.timestamp_offset = 0.0
        self.silence_threshold = 5
        self.frames = []
        self.transcribedText = '' 
        # Set up PyAudio
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=self.RATE,
                        input=True,
                        frames_per_buffer=self.CHUNK)
        
        self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

    def _is_webrtc_speech(self, data, all_frames_must_be_true=False):
        sample_rate = self.RATE
        # Number of audio frames per millisecond
        frame_length = int(sample_rate * 0.01)  # for 10ms frame
        num_frames = int(len(data) / (2 * frame_length))
        speech_frames = 0

        for i in range(num_frames):
            start_byte = i * frame_length * 2
            end_byte = start_byte + frame_length * 2
            frame = data[start_byte:end_byte]
            if self.webrtc_vad_model.is_speech(frame, sample_rate):
                speech_frames += 1
                if not all_frames_must_be_true:
                    return True
        if all_frames_must_be_true:
            return speech_frames == num_frames
        else:
            return False

    def transcribe(self, input_audio):
        # this works 
        audio = np.frombuffer(buffer=input_audio, dtype=np.int16)
        # Convert s16 back to f32.
        audio = audio.astype(np.float32) / 32768.0
        
        start = time.time()
        segments, info = self.model.transcribe(audio, beam_size=5, vad_filter=True, vad_parameters={"threshold": 0.5})
        print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
        for segment in segments:
            print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
            self.transcribedText+=(' '+segment.text)
        infer_time = time.time() - start

        print(f"Detection done in {infer_time} seconds")

    def recording(self):
        silence_start_time = None

        while True:
            # Read chunk of audio data
            data = self.stream.read(self.CHUNK)
            voice_detected = self._is_webrtc_speech(data)
            if voice_detected:
                print("Voice detected!")
                # add data to frames
                self.frames.append(data)
                self.on_recording = True
                silence_start_time = None  # reset the silence timer
            else:
                if self.on_recording:
                    print("Voice ended, transcribing...")
                    # concatenate frames
                    audio_data = b''.join(self.frames)

                    t = threading.Thread(
                        target=self.transcribe,
                        args=(
                            audio_data,
                        ),
                    )
                    t.start()

                    # clear frames
                    self.frames = []
                    self.on_recording = False
                    silence_start_time = time.time()  # start the silence timer
                else:
                    # print("No voice detected.")
                    # if no voice detected for 5 seconds, print whole setense
                    if silence_start_time and time.time() - silence_start_time > self.silence_threshold:  # 5 seconds of silence
                        if self.transcribedText:
                            print("%s seconds of silence detected. Send transcribed text." % (self.silence_threshold))
                            print(self.transcribedText)
                            self.transcribedText = ''  # reset the transcribed text
                        silence_start_time = time.time()  # reset the silence timer


if __name__ == "__main__":
    vad = VoiceRecorder()
    vad.recording()