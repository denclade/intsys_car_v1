import zmq
import numpy as np
import cv2

class SerializingSocket(zmq.Socket):
    """Numpy array serialization methods.

    Modelled on PyZMQ serialization examples.

    Used for sending / receiving OpenCV images, which are Numpy arrays.
    Also used for sending / receiving jpg compressed OpenCV images.
    """

    def send_img(self, frame, msg, quality=90):
        ret_code, img = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        self.send_array(img, msg)

    def enable_heartbeat(self, interval_ms=1000, timeout_ms=3000):
        """ZMTP-Heartbeat: eine tote Verbindung (Partner ohne Abmeldung weg) wird nach
        timeout_ms geschlossen; ein zmq.PAIR-Socket nimmt dann wieder einen neuen Partner an."""
        self.setsockopt(zmq.HEARTBEAT_IVL, interval_ms)
        self.setsockopt(zmq.HEARTBEAT_TIMEOUT, timeout_ms)
        self.setsockopt(zmq.HEARTBEAT_TTL, timeout_ms)

    def recv_img(self):
        msg, img = self.recv_array()
        img = cv2.imdecode(img, -1)
        return msg, img

    def send_array(self, arr, msg=None, flags=0, copy=True, track=False):
        """Sends a numpy array with metadata and text message.

        Sends a numpy array with the metadata necessary for reconstructing
        the array (dtype,shape). Also sends a text msg, often the array or
        image name.

        Arguments:
          arr: numpy array or OpenCV image.
          msg: (optional) Json data.
          flags: (optional) zmq flags.
          copy: (optional) zmq copy flag.
          track: (optional) zmq track flag.
        """

        if msg is None:
            msg = {}
        md = dict(
            msg=msg,
            dtype=str(arr.dtype),
            shape=arr.shape,
        )
        self.send_json(md, flags | zmq.SNDMORE)
        return self.send(arr, flags, copy=copy, track=track)

    def recv_array(self, flags=0, copy=True, track=False):
        """Receives a numpy array with metadata and text message.

        Receives a numpy array with the metadata necessary
        for reconstructing the array (dtype,shape).
        Returns the array and a text msg, often the array or image name.

        Arguments:
          flags: (optional) zmq flags.
          copy: (optional) zmq copy flag.
          track: (optional) zmq track flag.

        Returns:
          msg: JSON Object with additional data.
          A: numpy array or OpenCV image reconstructed with dtype and shape.
        """

        md = self.recv_json(flags=flags)
        msg = self.recv(flags=flags, copy=copy, track=track)
        A = np.frombuffer(msg, dtype=md['dtype'])
        return md['msg'], A.reshape(md['shape'])


class SerializingContext(zmq.Context):
    _socket_class = SerializingSocket