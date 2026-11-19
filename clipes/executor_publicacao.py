"""Executor periodico de publicacao: roda `run_once` numa thread daemon propria.

Mantem o upload (lento, YouTube/TikTok em serie) fora do heartbeat/loop principal, para que a renovacao de
lease nunca espere um upload (auditoria C-02). Usado pelo monitor e reaproveitavel por `acompanhar`/bot.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class PublishLoop:
    """Roda `run_once()` a cada `interval_s` (thread daemon) e/ou sob demanda via `run_now()`.

    - No maximo uma rodada por vez (Lock nao bloqueante): `run_now()` devolve False se ja ha uma rodando.
    - Excecoes de `run_once` sao logadas e engolidas: a thread nunca morre por causa de uma rodada.
    - `interval_s <= 0` desliga o timer; so `run_now()` dispara rodadas.
    """

    def __init__(self, run_once: Callable[[], None], interval_s: float, *, name: str = "publicacao"):
        self._run_once = run_once
        self.interval_s = float(interval_s)
        self.name = name
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._timer_thread: threading.Thread | None = None
        self._runs: set[threading.Thread] = set()
        self._runs_guard = threading.RLock()  # protege _runs e o flag de parada (run_now x stop)

    def _execute(self) -> None:
        """Corpo de uma rodada; chamador ja detem `self._lock`."""
        try:
            self._run_once()
        except Exception as exc:  # uma rodada ruim nao derruba a thread
            print(f"[{self.name}] erro na rodada: {type(exc).__name__}: {exc}", flush=True)
        finally:
            self._lock.release()

    def _spawn(self) -> None:
        def body() -> None:
            try:
                self._execute()
            finally:
                with self._runs_guard:
                    self._runs.discard(threading.current_thread())

        thread = threading.Thread(target=body, name=f"{self.name}-run", daemon=True)
        with self._runs_guard:
            self._runs.add(thread)
        thread.start()

    def run_now(self) -> bool:
        """Dispara uma rodada sem bloquear. False se ja ha uma rodando (ou o executor foi parado)."""
        with self._runs_guard:  # o flag de parada e o registro da thread mudam sob a mesma trava de stop()
            if self._stop.is_set() or not self._lock.acquire(blocking=False):
                return False
            try:
                self._spawn()
            except BaseException:
                self._lock.release()
                raise
            return True

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def _timer(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.run_now()

    def start(self) -> None:
        if self.interval_s <= 0 or (self._timer_thread is not None and self._timer_thread.is_alive()):
            return
        self._stop.clear()
        self._timer_thread = threading.Thread(target=self._timer, name=f"{self.name}-timer", daemon=True)
        self._timer_thread.start()

    def stop(self, timeout: float = 10.0) -> bool:
        """Para o timer e espera a rodada em andamento ate `timeout` s. True se nada ficou rodando."""
        threads: list[threading.Thread] = []
        with self._runs_guard:
            self._stop.set()
            if self._timer_thread is not None:
                threads.append(self._timer_thread)
            threads.extend(self._runs)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout)
        return not any(t.is_alive() for t in threads)
