# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.
import time
import redis

try:
    import gevent
except ImportError:
    gevent = None

import threading
from queue import Queue, SimpleQueue, Empty


class DualStageGeventSink(redis.Redis):
    """A sink is a Redis client where all commands are send-and-forget.
    Advantage:
        Commands return immediately, allowing caller to go back to work.
    Limitation:
        Commands should not expect an answer. The sink keep listening for ACKs
        from the server, but at the time it receives it, the caller is already gone.
        The sink is especially suited for XADD command which expects no return.

    Dual stage sinks send and receive in separate tasks. Thus, the sending one delegates
    ACK listening and never waits for Round-Trip-Time. It maximizes socket usage at the
    expense of more synchronization between the tasks.
    In a gevent sink, tasks are greenlets.

    Even when using commands that cannot fail, errors can happen on the network or on
    server side (Out-Of-Memory). This is one reason to parse response from the server.
    If an error happens, it is stored by the receiving task and will be raised on caller
    side on the next command, join, or stop.
    """

    def __init__(self, data_store):
        if gevent is None:
            raise RuntimeError(
                f"{type(self).__name__} requires gevent, install it or use thread-based version."
            )
        self._data_store = data_store
        super().__init__(
            connection_pool=self._data_store._redis.connection_pool,
            single_connection_client=True,
        )
        self._cmd_queue = gevent.queue.JoinableQueue()
        self._ack_queue = gevent.queue.Queue()

        self._closing = False
        self._closed = gevent.event.Event()
        self._failure = None

        self._send_glt = gevent.spawn(self._send_command_task)
        self._send_glt.name = "sink-send-loop"
        self._recv_glt = gevent.spawn(self._check_error_task)
        self._recv_glt.name = "sink-ack-loop"

    def join(self):
        """Block until all submitted commands are acknowledged by Redis"""
        self._cmd_queue.join()
        if self._failure:
            self._closed.wait()
            self._send_glt.get()
            self._recv_glt.get()
            raise self._failure

    def stop(self):
        self._closing = True
        self._cmd_queue.put(StopIteration)
        self._cmd_queue.task_done()
        self._cmd_queue.join()
        self._closed.wait()

        self._send_glt.get()
        self._recv_glt.get()
        if self._failure:
            raise self._failure

    def execute_command(self, *args, **kwargs):
        if not (self._closing or self._closed.is_set()):
            # force NEVER DECODE to fasten response parsing
            kwargs[redis.client.NEVER_DECODE] = []
            self._cmd_queue.put((args, kwargs))
        else:
            self._send_glt.get()
            self._recv_glt.get()
            if self._failure:
                # possibly raise the failure that closed the sink
                raise self._failure
            # otherwise just raise because its closed
            raise Exception("Writing to a closed sink")

    def _send(self, commands):
        packed_commands = self.connection.pack_commands([args for args, _ in commands])
        self.connection.send_packed_command(packed_commands)
        self._ack_queue.put(commands)

    def _send_command_task(self):
        wait_interval = 0.01  # AKA min latency
        max_cumul_wait = 0.1  # AKA max latency
        cmd_buf = []
        while True:
            if not cmd_buf:
                item = self._cmd_queue.get()
                if item is StopIteration:
                    break
                cmd_buf.append(item)
                # reset timer
                last_send = time.perf_counter()
            else:
                try:
                    item = self._cmd_queue.get(timeout=wait_interval)
                    if item is StopIteration:
                        # if StopIteration comes from a failure, stop immediately
                        if not self._failure:
                            self._send(cmd_buf)
                        break
                    else:
                        cmd_buf.append(item)
                except gevent.queue.Empty:
                    # send because no new command arrived in wait_interval
                    self._send(cmd_buf)
                    last_send = time.perf_counter()
                    cmd_buf = []
                else:
                    now = time.perf_counter()
                    if (now - last_send) >= max_cumul_wait:
                        # send because the oldest command waited max_cumul_wait
                        self._send(cmd_buf)
                        last_send = now
                        cmd_buf = []
        self._ack_queue.put(StopIteration)

    def _check_error_task(self):
        for commands in self._ack_queue:
            for args, options in commands:
                try:
                    self.parse_response(self.connection, args[0], **options)
                    self._cmd_queue.task_done()
                except redis.exceptions.ResponseError as e:
                    self._failure = e

                    # reset queue
                    while self._cmd_queue.unfinished_tasks:
                        self._cmd_queue.task_done()
                    self._cmd_queue.put(StopIteration)
                    self._cmd_queue.task_done()

                    self._send_glt.join()
                    self.close()
                    self._closed.set()
                    return
        self.close()
        self._closed.set()


class SingleStageGeventSink(redis.Redis):
    """A sink is a Redis client where all commands are send-and-forget.
    Advantage:
        Commands return immediately, allowing caller to go back to work.
    Limitation:
        Commands should not expect an answer. The sink keep listening for ACKs
        from the server, but at the time it receives it, the caller is already gone.
        The sink is especially suited for XADD command which expects no return.

    Single stage sinks use a single background task to send and receive. It sends Redis
    pipelines normally, i.e. it waits for the previous ACK before sending a new one.
    In a gevent sink, tasks are greenlets.

    Even when using commands that cannot fail, errors can happen on the network or on
    server side (Out-Of-Memory). This is one reason to parse response from the server.
    If an error happens, it is stored by the receiving task and will be raised on caller
    side on the next command, join, or stop.
    """

    def __init__(self, data_store):
        if gevent is None:
            raise RuntimeError(
                f"{type(self).__name__} requires gevent, install it or use thread-based version."
            )
        self._data_store = data_store
        super().__init__(
            connection_pool=self._data_store._redis.connection_pool,
            single_connection_client=True,
        )
        self._cmd_queue = gevent.queue.JoinableQueue()
        self._failure = None
        self._closed = False
        self._send_glt = gevent.spawn(self._send_command_task)
        self._send_glt.name = "sink-send-loop"

    def join(self):
        self._cmd_queue.join()
        if self._failure:
            self._send_glt.get()
            raise self._failure

    def stop(self):
        self._closed = True
        self._cmd_queue.put(StopIteration)
        self._cmd_queue.task_done()
        self.join()

    def execute_command(self, *args, **kwargs):
        if self._failure:
            self._send_glt.get()
            raise self._failure
        elif self._closed:
            raise Exception("Writing to a closed sink")
        else:
            # force NEVER DECODE
            kwargs[redis.client.NEVER_DECODE] = []
            self._cmd_queue.put((args, kwargs))

    def _send(self, commands):
        packed_commands = self.connection.pack_commands([args for args, _ in commands])
        self.connection.send_packed_command(packed_commands)
        for args, options in commands:
            try:
                self.parse_response(self.connection, args[0], **options)
                self._cmd_queue.task_done()
            except redis.exceptions.ResponseError as e:
                self._failure = e
                break

    def _send_command_task(self):
        wait_interval = 0.01  # AKA min latency
        max_cumul_wait = 0.1  # AKA max latency
        cmd_buf = []
        while not self._failure:
            if not cmd_buf:
                item = self._cmd_queue.get()
                if item is StopIteration:
                    break
                cmd_buf.append(item)
                # reset timer
                last_send = time.perf_counter()
            else:
                try:
                    item = self._cmd_queue.get(timeout=wait_interval)
                    if item is StopIteration:
                        self._send(cmd_buf)
                        break
                    cmd_buf.append(item)
                except Empty:
                    # send because no new command arrived in wait_interval
                    self._send(cmd_buf)
                    last_send = time.perf_counter()
                    cmd_buf = []
                else:
                    now = time.perf_counter()
                    if (now - last_send) >= max_cumul_wait:
                        # send because the oldest command waited max_cumul_wait
                        self._send(cmd_buf)
                        last_send = now
                        cmd_buf = []

        # reset queue
        while self._cmd_queue.unfinished_tasks:
            self._cmd_queue.task_done()
        self.close()


class DualStageThreadSink(redis.Redis):
    """A sink is a Redis client where all commands are send-and-forget.
    Advantage:
        Commands return immediately, allowing caller to go back to work.
    Limitation:
        Commands should not expect an answer. The sink keep listening for ACKs
        from the server, but at the time it receives it, the caller is already gone.
        The sink is especially suited for XADD command which expects no return.

    Dual stage sinks send and receive in separate tasks. Thus, the sending one delegates
    ACK listening and never waits for Round-Trip-Time. It maximizes socket usage at the
    expense of more synchronization between the tasks.
    In a thread sink, tasks are python threads.

    Even when using commands that cannot fail, errors can happen on the network or on
    server side (Out-Of-Memory). This is one reason to parse response from the server.
    If an error happens, it is stored by the receiving task and will be raised on caller
    side on the next command, join, or stop.
    """

    def __init__(self, data_store):
        self._data_store = data_store
        super().__init__(
            connection_pool=self._data_store._redis.connection_pool,
            single_connection_client=True,
        )
        self.cond = threading.Condition()
        self._cmd_queue = Queue()
        self._ack_queue = SimpleQueue()
        self._closing = False
        self._closed = False
        self._failure = None

        self._send_thread = threading.Thread(target=self._send_command_task)
        self._recv_thread = threading.Thread(target=self._check_error_task)
        self._send_thread.start()
        self._recv_thread.start()

    def join(self):
        """Block until all submitted commands are acknowledged by Redis"""
        self._cmd_queue.join()
        # join can finish because of the queue reset
        # then take lock to wait for failure processing to end
        with self.cond:
            if self._failure:
                raise self._failure

    def stop(self):
        with self.cond:
            self._closing = True
            self._cmd_queue.put(StopIteration)
            self._cmd_queue.task_done()
            while not self._closed:
                self.cond.wait()
        if self._failure:
            raise self._failure

    def execute_command(self, *args, **kwargs):
        with self.cond:
            if not (self._closing or self._closed):
                # force NEVER DECODE to fasten response parsing
                kwargs[redis.client.NEVER_DECODE] = []
                self._cmd_queue.put((args, kwargs))
            elif self._failure:
                raise self._failure
            else:
                raise Exception("Writing to a closed sink")

    def _send(self, commands):
        packed_commands = self.connection.pack_commands([args for args, _ in commands])
        self.connection.send_packed_command(packed_commands)
        self._ack_queue.put(commands)

    def _send_command_task(self):
        wait_interval = 0.01  # AKA min latency
        max_cumul_wait = 0.1  # AKA max latency
        cmd_buf = []
        while True:
            if not cmd_buf:
                item = self._cmd_queue.get()
                if item is StopIteration:
                    break
                cmd_buf.append(item)
                # reset timer
                last_send = time.perf_counter()
            else:
                try:
                    item = self._cmd_queue.get(timeout=wait_interval)
                    if item is StopIteration:
                        # if StopIteration comes from a failure, stop immediately
                        if not self._failure:
                            self._send(cmd_buf)
                        break
                    else:
                        cmd_buf.append(item)
                except Empty:
                    # send because no new command arrived in wait_interval
                    self._send(cmd_buf)
                    last_send = time.perf_counter()
                    cmd_buf = []
                else:
                    now = time.perf_counter()
                    if (now - last_send) >= max_cumul_wait:
                        # send because the oldest command waited max_cumul_wait
                        self._send(cmd_buf)
                        last_send = now
                        cmd_buf = []
        self._ack_queue.put(StopIteration)

    def _check_error_task(self):
        while True:
            commands = self._ack_queue.get()
            if commands is StopIteration:
                with self.cond:
                    self._closed = True
                    self.cond.notify_all()
                self.close()
                return
            for args, options in commands:
                try:
                    self.parse_response(self.connection, args[0], **options)
                    self._cmd_queue.task_done()
                except redis.exceptions.ResponseError as e:
                    with self.cond:
                        self._failure = e

                        # reset queue
                        with self._cmd_queue.not_empty:
                            self._cmd_queue.queue.clear()
                            self._cmd_queue.queue.append(StopIteration)
                            self._cmd_queue.not_empty.notify()
                        with self._cmd_queue.all_tasks_done:
                            self._cmd_queue.unfinished_tasks = 0
                            self._cmd_queue.all_tasks_done.notify_all()

                        self._send_thread.join()
                        self._closed = True
                        self.cond.notify_all()
                        self.close()
                        return


class SingleStageThreadSink(redis.Redis):
    """A sink is a Redis client where all commands are send-and-forget.
    Advantage:
        Commands return immediately, allowing caller to go back to work.
    Limitation:
        Commands should not expect an answer. The sink keep listening for ACKs
        from the server, but at the time it receives it, the caller is already gone.
        The sink is especially suited for XADD command which expects no return.

    Single stage sinks use a single background task to send and receive. It sends Redis
    pipelines normally, i.e. it waits for the previous ACK before sending a new one.
    In a thread sink, tasks are python threads.

    Even when using commands that cannot fail, errors can happen on the network or on
    server side (Out-Of-Memory). This is one reason to parse response from the server.
    If an error happens, it is stored by the receiving task and will be raised on caller
    side on the next command, join, or stop.
    """

    def __init__(self, data_store):
        self._data_store = data_store
        super().__init__(
            connection_pool=self._data_store._redis.connection_pool,
            single_connection_client=True,
        )
        self._cmd_queue = Queue()
        self._failure = None
        self._closed = False
        self._lock = threading.Lock()
        self._send_thread = threading.Thread(target=self._send_command_task)
        self._send_thread.start()

    def join(self):
        self._cmd_queue.join()
        if self._failure:
            raise self._failure

    def stop(self):
        with self._lock:
            self._closed = True
            self._cmd_queue.put(StopIteration)
            self._cmd_queue.task_done()
        self.join()

    def execute_command(self, *args, **kwargs):
        with self._lock:
            if self._failure:
                raise self._failure
            elif self._closed:
                raise Exception("Writing to a closed sink")
            else:
                # force NEVER DECODE
                kwargs[redis.client.NEVER_DECODE] = []
                self._cmd_queue.put((args, kwargs))

    def _send(self, commands):
        packed_commands = self.connection.pack_commands([args for args, _ in commands])
        self.connection.send_packed_command(packed_commands)
        for args, options in commands:
            try:
                self.parse_response(self.connection, args[0], **options)
                self._cmd_queue.task_done()
            except redis.exceptions.ResponseError as e:
                self._failure = e
                break

    def _send_command_task(self):
        wait_interval = 0.01  # AKA min latency
        max_cumul_wait = 0.1  # AKA max latency
        cmd_buf = []
        while not self._failure:
            if not cmd_buf:
                item = self._cmd_queue.get()
                if item is StopIteration:
                    break
                cmd_buf.append(item)
                # reset timer
                last_send = time.perf_counter()
            else:
                try:
                    item = self._cmd_queue.get(timeout=wait_interval)
                    if item is StopIteration:
                        self._send(cmd_buf)
                        break
                    cmd_buf.append(item)
                except Empty:
                    # send because no new command arrived in wait_interval
                    self._send(cmd_buf)
                    last_send = time.perf_counter()
                    cmd_buf = []
                else:
                    now = time.perf_counter()
                    if (now - last_send) >= max_cumul_wait:
                        # send because the oldest command waited max_cumul_wait
                        self._send(cmd_buf)
                        last_send = now
                        cmd_buf = []

        with self._lock:
            # reset queue
            with self._cmd_queue.all_tasks_done:
                # validate all the tasks for new join() to not block
                self._cmd_queue.unfinished_tasks = 0
                # notify all running join() for them to leave
                self._cmd_queue.all_tasks_done.notify_all()
        self.close()
