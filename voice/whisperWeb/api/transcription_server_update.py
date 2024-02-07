import websockets
import time
import threading
import json
import textwrap

import logging
logging.basicConfig(level = logging.INFO)

from websockets.sync.server import serve
from vad import VoiceActivityDetection

import torch
import numpy as np
import time
from transcriber import WhisperModel
import os
import functools
import queue
import platform

os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"


class TranscriptionServer:
    """
    Represents a transcription server that handles incoming audio from clients.

    Attributes:
        RATE (int): The audio sampling rate (constant) set to 16000.
        vad_model (torch.Module): The voice activity detection model.
        vad_threshold (float): The voice activity detection threshold.
        clients (dict): A dictionary to store connected clients.
        websockets (dict): A dictionary to store WebSocket connections.
        clients_start_time (dict): A dictionary to track client start times.
        max_clients (int): Maximum allowed connected clients.
        max_connection_time (int): Maximum allowed connection time in seconds.
    """

    RATE = 16000

    def __init__(self):
        # voice activity detection model

        self.clients = {}
        self.websockets = {}
        self.clients_start_time = {}
        self.max_clients = 4
        self.max_connection_time = 600
        self.transcriber = None

    def get_wait_time(self):
        """
        Calculate and return the estimated wait time for clients.

        Returns:
            float: The estimated wait time in minutes.
        """
        wait_time = None

        for k, v in self.clients_start_time.items():
            current_client_time_remaining = self.max_connection_time - (time.time() - v)

            if wait_time is None or current_client_time_remaining < wait_time:
                wait_time = current_client_time_remaining

        return wait_time / 60

    def options_processing(self, websocket):
        options = websocket.recv()
        options = json.loads(options)
        return options

    def handle_client_queue(self, websocket, options):
        if len(self.clients) >= self.max_clients:
            logging.warning("Client Queue Full. Asking client to wait ...")
            wait_time = self.get_wait_time()
            response = {
                "uid": options["uid"],
                "status": "WAIT",
                "message": wait_time,
            }
            websocket.send(json.dumps(response))
            websocket.close()
            del websocket
            return

    def handle_transcriber(self):
        device = "cpu"
        compute = "int8"
        if self.transcriber is None:
            if torch.cuda.is_available() and 'ubuntu' in platform.platform().lower():
                device = "cuda"
                compute = "float16"

            self.transcriber = WhisperModel(
                model_size_or_path="base.en",
                device=device,
                compute_type=compute,
                local_files_only=False,
            )
        return device, compute


    def assign_client(self, websocket, options, transcription_queue=None, llm_queue=None):
        client = ServeClient(
            websocket,
            multilingual=options["multilingual"],
            language=options["language"],
            task=options["task"],
            client_uid=options["uid"],
            transcription_queue=transcription_queue,
            llm_queue=llm_queue,
            transcriber=self.transcriber
        )

        self.clients[websocket] = client
        self.clients_start_time[websocket] = time.time()
        return client

    def frame_processing(self, websocket, client):
        try:
            frame_data = websocket.recv()
            frame_np = np.frombuffer(frame_data, dtype=np.float32)
            return frame_data, frame_np
        except Exception as e:
            logging.error(e)
            del websocket
            return


    def voice_activity_detection(self, frame_np, no_voice_activity_chunks, websocket):
        """Detects voice activity in an audio frame.
    
        Uses a pretrained Voice Activity Detection (VAD) model to determine if an audio 
        frame contains speech. Keeps track of consecutive silent frames and sets end-of-speech
        flag on client if threshold is exceeded. Returns number of silent frames and a 
        flag indicating if frame contains speech.
        
        Args:
            frame_np: Numpy array containing audio frame data.
            no_voice_activity_chunks: Tracker for number of consecutive silent frames.
            websocket: Websocket connection to client.
        
        Returns:
            no_voice_activity_chunks: Updated count of consecutive silent frames.
            continue_processing: Flag indicating if frame contains speech.
        """
        try:
            speech_prob = self.vad_model(torch.from_numpy(frame_np.copy()), self.RATE).item()
            if speech_prob < self.vad_threshold:
                no_voice_activity_chunks += 1
                if no_voice_activity_chunks > 3:
                    if not self.clients[websocket].eos:
                        self.clients[websocket].set_eos(True)
                    time.sleep(0.1)
                return no_voice_activity_chunks, False
            no_voice_activity_chunks = 0
            self.clients[websocket].set_eos(False)
            return no_voice_activity_chunks, True

        except Exception as e:
            logging.error(e)
            return no_voice_activity_chunks, False


    def disconnect_client(self, websocket):
        self.clients[websocket].disconnect()
        logging.warning(f"{self.clients[websocket]} Client disconnected due to overtime.")
        self.clients[websocket].cleanup()
        self.clients.pop(websocket)
        self.clients_start_time.pop(websocket)
        websocket.close()
        del websocket

    """Receives audio data from a websocket client.
    
    Performs voice activity detection, segments audio into frames, runs speech recognition
    with Whisper, and sends transcription results back to the client. Handles connecting 
    and disconnecting clients.
    """
    def recv_audio(self, websocket, transcription_queue=None, llm_queue=None, whisper_tensorrt_path=None):
        logging.info("[Transcription:] New client connected")

        self.vad_model = VoiceActivityDetection()
        self.vad_threshold = 0.5

        options = self.options_processing(websocket)
        self.handle_client_queue(websocket, options)

        device, compute = self.handle_transcriber()

        client = self.assign_client(websocket, options, transcription_queue, llm_queue)

        no_voice_activity_chunks = 0

        while True:
            frame_data, frame_np = self.frame_processing(websocket, client)
            no_voice_activity_chunks, continue_processing = self.voice_activity_detection(frame_np, no_voice_activity_chunks, websocket)
            if not continue_processing:
                continue
            self.clients[websocket].add_frames(frame_np)

            elapsed_time = time.time() - self.clients_start_time[websocket]
            if elapsed_time >= self.max_connection_time:
                self.disconnect_client(websocket)
                break

    def run(self, host, port=9090, transcription_queue=None, llm_queue=None):
        """
        Run the transcription server.

        Args:
            host (str): The host address to bind the server.
            port (int): The port number to bind the server.
        """
        with serve(
            functools.partial(
                self.recv_audio,
                transcription_queue=transcription_queue,
                llm_queue=llm_queue,
            ),
            host,
            port
        ) as server:
            server.serve_forever()


class ServeClient:
    """
    Attributes:
        RATE (int): The audio sampling rate (constant) set to 16000.
        SERVER_READY (str): A constant message indicating that the server is ready.
        DISCONNECT (str): A constant message indicating that the client should disconnect.
        client_uid (str): A unique identifier for the client.
        data (bytes): Accumulated audio data.
        frames (bytes): Accumulated audio frames.
        language (str): The language for transcription.
        task (str): The task type, e.g., "transcribe."
        transcriber (WhisperModel): The Whisper model for speech-to-text.
        timestamp_offset (float): The offset in audio timestamps.
        frames_np (numpy.ndarray): NumPy array to store audio frames.
        frames_offset (float): The offset in audio frames.
        text (list): List of transcribed text segments.
        current_out (str): The current incomplete transcription.
        prev_out (str): The previous incomplete transcription.
        t_start (float): Timestamp for the start of transcription.
        exit (bool): A flag to exit the transcription thread.
        same_output_threshold (int): Threshold for consecutive same output segments.
        show_prev_out_thresh (int): Threshold for showing previous output segments.
        add_pause_thresh (int): Threshold for adding a pause (blank) segment.
        transcript (list): List of transcribed segments.
        send_last_n_segments (int): Number of last segments to send to the client.
        wrapper (textwrap.TextWrapper): Text wrapper for formatting text.
        pick_previous_segments (int): Number of previous segments to include in the output.
        websocket: The WebSocket connection for the client.
    """
    RATE = 16000
    SERVER_READY = "SERVER_READY"
    DISCONNECT = "DISCONNECT"

    def __init__(
        self,
        websocket,
        task="transcribe",
        device=None,
        multilingual=False,
        language=None, 
        client_uid=None,
        transcription_queue=None,
        llm_queue=None,
        transcriber=None
        ):
        """
        Initialize a ServeClient instance.
        The Whisper model is initialized based on the client's language and device availability.
        The transcription thread is started upon initialization. A "SERVER_READY" message is sent
        to the client to indicate that the server is ready.

        Args:
            websocket (WebSocket): The WebSocket connection for the client.
            task (str, optional): The task type, e.g., "transcribe." Defaults to "transcribe".
            device (str, optional): The device type for Whisper, "cuda" or "cpu". Defaults to None.
            multilingual (bool, optional): Whether the client supports multilingual transcription. Defaults to False.
            language (str, optional): The language for transcription. Defaults to None.
            client_uid (str, optional): A unique identifier for the client. Defaults to None.

        """
        if transcriber is None:
            raise ValueError("Transcriber is None.")
        self.transcriber = transcriber
        self.client_uid = client_uid
        self.transcription_queue = transcription_queue
        self.llm_queue = llm_queue
        self.data = b""
        self.frames = b""
        self.language = language if multilingual else "en"
        self.task = task
        self.last_prompt = None
        
        
        self.timestamp_offset = 0.0
        self.frames_np = None
        self.frames_offset = 0.0

        self.exit = False
        self.transcript = []
        self.prompt = None
        self.segment_inference_time = []

        self.text = []
        self.current_out = ''
        self.prev_out = ''
        self.t_start=None

        self.same_output_threshold = 0
        self.show_prev_out_thresh = 5   # if pause(no output from whisper) show previous output for 5 seconds
        self.add_pause_thresh = 3       # add a blank to segment list as a pause(no speech) for 3 seconds

        self.send_last_n_segments = 10

        # text formatting
        self.wrapper = textwrap.TextWrapper(width=50)
        self.pick_previous_segments = 2

        # threading
        self.websocket = websocket
        self.lock = threading.Lock()
        self.eos = False
        self.trans_thread = threading.Thread(target=self.speech_to_text)
        self.trans_thread.start()
        self.websocket.send(
            json.dumps(
                {
                    "uid": self.client_uid,
                    "message": self.SERVER_READY
                }
            )
        )

    def set_eos(self, eos):
        self.lock.acquire()
        self.eos = eos
        self.lock.release()

    def fill_output(self, output):
        """
        Format the current incomplete transcription output by combining it with previous complete segments.
        The resulting transcription is wrapped into two lines, each containing a maximum of 50 characters.

        It ensures that the combined transcription fits within two lines, with a maximum of 50 characters per line.
        Segments are concatenated in the order they exist in the list of previous segments, with the most
        recent complete segment first and older segments prepended as needed to maintain the character limit.
        If a 3-second pause is detected in the previous segments, any text preceding it is discarded to ensure
        the transcription starts with the most recent complete content. The resulting transcription is returned
        as a single string.

        Args:
            output(str): The current incomplete transcription segment.
        
        Returns:
            str: A formatted transcription wrapped in two lines.
        """
        text = ''
        pick_prev = min(len(self.text), self.pick_previous_segments)
        for seg in self.text[-pick_prev:]:
            # discard everything before a 3 second pause
            if seg == '':
                text = ''
            else:
                text += seg
        wrapped = "".join(text + output)
        return wrapped
    
    def add_frames(self, frame_np):
        """
        Add audio frames to the ongoing audio stream buffer.

        This method is responsible for maintaining the audio stream buffer, allowing the continuous addition
        of audio frames as they are received. It also ensures that the buffer does not exceed a specified size
        to prevent excessive memory usage.

        If the buffer size exceeds a threshold (45 seconds of audio data), it discards the oldest 30 seconds
        of audio data to maintain a reasonable buffer size. If the buffer is empty, it initializes it with the provided
        audio frame. The audio stream buffer is used for real-time processing of audio data for transcription.

        Args:
            frame_np (numpy.ndarray): The audio frame data as a NumPy array.

        """
        self.lock.acquire()

        if self.frames_np is not None and self.frames_np.shape[0] > 45*self.RATE:
            self.frames_offset += 30.0
            self.frames_np = self.frames_np[int(30*self.RATE):]
        if self.frames_np is None:
            self.frames_np = frame_np.copy()
        else:
            self.frames_np = np.concatenate((self.frames_np, frame_np), axis=0)
        self.lock.release()

    def check_llm_queue(self):
        llm_response = None
        if self.llm_queue is not None:
            while not self.llm_queue.empty():
                llm_response = self.llm_queue.get()
            if llm_response:
                eos = llm_response["eos"]
                if eos:
                    return llm_response
        return None

    def process_audio(self):
        samples_take = max(0, (self.timestamp_offset - self.frames_offset) * self.RATE)
        input_bytes = self.frames_np[int(samples_take):].copy()
        duration = input_bytes.shape[0] / self.RATE
        return input_bytes, duration

    def whisper_transcribe(self, input_sample):
        start = time.time()
        result, info = self.transcriber.transcribe(
            input_sample,
            initial_prompt=None,
            language=self.language,
            task=self.task,
            vad_filter=True,
            vad_parameters={"threshold": 0.5}
        )
        infer_time = time.time() - start
        return result, info, infer_time

    def update_prompt_and_segments(self, result, duration):
        if len(result):
            self.t_start = None
            last_segment = self.update_segments(result, duration)
            if len(self.transcript) < self.send_last_n_segments:
                segments = self.transcript
            else:
                segments = self.transcript[-self.send_last_n_segments:]
            if last_segment is not None:
                segments = segments + [last_segment]
            return segments, last_segment
        else:
            segments = []
            if self.t_start is None:
                self.t_start = time.time()
            if time.time() - self.t_start < self.show_prev_out_thresh:
                if len(self.transcript) < self.send_last_n_segments:
                    segments = self.transcript
                else:
                    segments = self.transcript[-self.send_last_n_segments:]
        return segments, None

    def update_prompt_for_pause(self, segments):
        if len(self.text) and self.text[-1] != '':
            if time.time() - self.t_start > self.add_pause_thresh:
                self.text.append('')
        self.prompt = ' '.join(segment['text'] for segment in segments)
        return self.prompt


    def return_segments(self, segments, infer_time):
        self.websocket.send(
            json.dumps({
                "uid": self.client_uid,
                "segments": segments,
                "eos": self.eos,
                "latency": infer_time
            })
        )
        self.transcription_queue.put({"uid": self.client_uid, "prompt": self.prompt, "eos": self.eos})


    def speech_to_text(self):
        """
        Transcribes audio to text using Whisper in a loop. 
        
        Processes incoming audio in chunks, runs Whisper inference, 
        updates transcript with new segments, sends updates to client.
        
        Manages state like current prompt, transcript, timestamps, etc.
        to enable real-time streaming transcription.
        """
        while True:
            llm_response = self.check_llm_queue()
            if llm_response:
                logging.info(f"[Transcription]: Sending LLM response to web socket")
                self.websocket.send(json.dumps(llm_response))

            if self.exit:
                logging.info("[Transcription]: Exiting speech to text thread")
                break

            if self.frames_np is None:
                time.sleep(0.02)
                continue

            if self.frames_np[int((self.timestamp_offset - self.frames_offset)*self.RATE):].shape[0] > 25 * self.RATE:
                duration = self.frames_np.shape[0] / self.RATE
                self.timestamp_offset = self.frames_offset + duration - 5

            input_bytes, duration = self.process_audio()
            if duration < 0.4:
                time.sleep(0.01)
                continue
            
            result, info, infer_time = self.whisper_transcribe(input_bytes)

            segments, last_segment = self.update_prompt_and_segments(result, duration)

            self.prompt = self.update_prompt_for_pause(segments)

            try:
                self.return_segments(segments, infer_time)
                logging.info(f"[Transcription]: Send message to transcription queue {self.prompt}")
                if self.eos:
                    self.timestamp_offset += duration
                    logging.info(f"[Transcription]: EOS: {self.eos} Prompt: {self.prompt}")
                    logging.info(
                        f"[Transcription]: Average inference time {sum(self.segment_inference_time) / len(self.segment_inference_time)}\n")
                    self.segment_inference_time = []

            except Exception as e:
                logging.error(f"[Transcription ERROR]: {e}")

    
    def update_segments(self, segments, duration):
        """
        Processes the segments from whisper. Appends all the segments to the list
        except for the last segment assuming that it is incomplete.

        Updates the ongoing transcript with transcribed segments, including their start and end times.
        Complete segments are appended to the transcript in chronological order. Incomplete segments 
        (assumed to be the last one) are processed to identify repeated content. If the same incomplete 
        segment is seen multiple times, it updates the offset and appends the segment to the transcript.
        A threshold is used to detect repeated content and ensure it is only included once in the transcript.
        The timestamp offset is updated based on the duration of processed segments. The method returns the 
        last processed segment, allowing it to be sent to the client for real-time updates.

        Args:
            segments(dict) : dictionary of segments as returned by whisper
            duration(float): duration of the current chunk
        
        Returns:
            dict or None: The last processed segment with its start time, end time, and transcribed text.
                     Returns None if there are no valid segments to process.
        """
        offset = None
        self.current_out = ''
        last_segment = None
        # process complete segments
        if len(segments) > 1:
            for i, s in enumerate(segments[:-1]):
                text_ = s.text
                self.text.append(text_)
                start, end = self.timestamp_offset + s.start, self.timestamp_offset + min(duration, s.end)
                self.transcript.append(
                    {
                        'start': start,
                        'end': end,
                        'text': text_
                    }
                )
                
                offset = min(duration, s.end)

        self.current_out += segments[-1].text
        last_segment = {
            'start': self.timestamp_offset + segments[-1].start,
            'end': self.timestamp_offset + min(duration, segments[-1].end),
            'text': self.current_out
        }
        
        # if same incomplete segment is seen multiple times then update the offset
        # and append the segment to the list
        if self.current_out.strip() == self.prev_out.strip() and self.current_out != '': 
            self.same_output_threshold += 1
        else: 
            self.same_output_threshold = 0
        
        if self.same_output_threshold > 5:
            if not len(self.text) or self.text[-1].strip().lower()!=self.current_out.strip().lower():          
                self.text.append(self.current_out)
                self.transcript.append(
                    {
                        'start': self.timestamp_offset,
                        'end': self.timestamp_offset + duration,
                        'text': self.current_out
                    }
                )
            self.current_out = ''
            offset = duration
            self.same_output_threshold = 0
            last_segment = None
        else:
            self.prev_out = self.current_out
        
        # update offset
        if offset is not None:
            self.timestamp_offset += offset

        return last_segment
    
    def disconnect(self):
        """
        Notify the client of disconnection and send a disconnect message.

        This method sends a disconnect message to the client via the WebSocket connection to notify them
        that the transcription service is disconnecting gracefully.

        """
        self.websocket.send(
            json.dumps(
                {
                    "uid": self.client_uid,
                    "message": self.DISCONNECT
                }
            )
        )
    
    def cleanup(self):
        """
        Perform cleanup tasks before exiting the transcription service.

        This method performs necessary cleanup tasks, including stopping the transcription thread, marking
        the exit flag to indicate the transcription thread should exit gracefully, and destroying resources
        associated with the transcription process.

        """
        logging.info("Cleaning up.")
        self.exit = True
        # ERROR:root:[ERROR]: 'WhisperModel' object has no attribute 'model'
        # possible error so remove this
        # self.transcriber.destroy()