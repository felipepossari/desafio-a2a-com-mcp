"""Dominio da Central de Salas: logica pura, sem rede e sem SDK.

Cinco salas, uma lista de reservas e tres regras de uso. As reservas vivem em
memoria; os dados iniciais vem dos arquivos de `dados/` (somente leitura).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

TZ_SP = timezone(timedelta(hours=-3))
ABERTURA = time(8, 0)
FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)
MAX_ALTERNATIVAS = 3

ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"


class ErroDeDominio(Exception):
    """Falha esperada de regra de negocio; vira `isError: true` no MCP."""


def erro_sala(sala: str) -> str:
    return f"Sala inexistente: {sala}"


@dataclass(frozen=True)
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: tuple[str, ...]


@dataclass(frozen=True)
class Reserva:
    id: str
    sala: str
    inicio: str
    fim: str
    responsavel: str

    @property
    def t_inicio(self) -> datetime:
        return parse_data(self.inicio)

    @property
    def t_fim(self) -> datetime:
        return parse_data(self.fim)


def parse_data(valor: str) -> datetime:
    """ISO 8601 com fuso. Sem fuso e invalido (a politica fala em -03:00)."""
    try:
        texto = valor.strip()
        if texto.endswith(("Z", "z")):
            texto = texto[:-1] + "+00:00"
        data = datetime.fromisoformat(texto)
    except (ValueError, AttributeError):
        raise ErroDeDominio(f"Data invalida: {valor!r}; use ISO 8601 com fuso, ex.: 2026-11-03T09:00:00-03:00") from None
    if data.tzinfo is None or data.utcoffset() is None:
        raise ErroDeDominio(f"Data invalida: {valor!r}; informe o fuso, ex.: 2026-11-03T09:00:00-03:00")
    return data


@dataclass
class Dominio:
    salas: list[Sala]
    reservas: list[Reserva]
    politica_texto: str
    _proximo: int = field(default=1)

    # ---- carga -----------------------------------------------------------
    @classmethod
    def carregar(cls, pasta: Path) -> "Dominio":
        salas = [
            Sala(s["id"], s["nome"], int(s["capacidade"]), tuple(s["recursos"]))
            for s in json.loads((pasta / "salas.json").read_text(encoding="utf-8"))
        ]
        reservas = [
            Reserva(r["id"], r["sala"], r["inicio"], r["fim"], r["responsavel"])
            for r in json.loads((pasta / "reservas.json").read_text(encoding="utf-8"))
        ]
        texto = (pasta / "politica-de-uso.md").read_text(encoding="utf-8")
        dom = cls(salas, reservas, texto)
        numeros = [int(r.id.rsplit("-", 1)[-1]) for r in reservas if r.id.rsplit("-", 1)[-1].isdigit()]
        dom._proximo = max(numeros, default=0) + 1
        return dom

    @property
    def versao_politica(self) -> str:
        primeira = self.politica_texto.splitlines()[0] if self.politica_texto else ""
        return primeira.split(":", 1)[1].strip() if ":" in primeira else primeira.strip()

    # ---- consultas ---------------------------------------------------------
    def sala(self, sala_id: str) -> Sala:
        for s in self.salas:
            if s.id == sala_id:
                return s
        raise ErroDeDominio(erro_sala(sala_id))

    def validar(self, sala_id: str, inicio: str, fim: str) -> tuple[Sala, datetime, datetime]:
        """Ordem fixa: sala, intervalo, janela de uso, duracao."""
        sala = self.sala(sala_id)
        t_ini, t_fim = parse_data(inicio), parse_data(fim)
        if t_fim <= t_ini:
            raise ErroDeDominio(ERRO_INTERVALO)
        local_ini, local_fim = t_ini.astimezone(TZ_SP), t_fim.astimezone(TZ_SP)
        if (
            local_ini.date() != local_fim.date()
            or local_ini.time() < ABERTURA
            or local_fim.time() > FECHAMENTO
        ):
            raise ErroDeDominio(ERRO_JANELA)
        if t_fim - t_ini > DURACAO_MAXIMA:
            raise ErroDeDominio(ERRO_DURACAO)
        return sala, t_ini, t_fim

    def conflitos(self, sala_id: str, t_ini: datetime, t_fim: datetime) -> list[Reserva]:
        """Sobreposicao estrita: [14h,15h] e [15h,16h] nao conflitam."""
        return [
            r
            for r in self.reservas
            if r.sala == sala_id and r.t_inicio < t_fim and t_ini < r.t_fim
        ]

    def alternativas(self, sala: Sala, t_ini: datetime, t_fim: datetime) -> list[str]:
        """Salas livres com capacidade >= a pedida, no maximo 3, por capacidade e id."""
        candidatas = [
            s
            for s in self.salas
            if s.id != sala.id and s.capacidade >= sala.capacidade and not self.conflitos(s.id, t_ini, t_fim)
        ]
        candidatas.sort(key=lambda s: (s.capacidade, s.id))
        return [s.id for s in candidatas[:MAX_ALTERNATIVAS]]

    # ---- escrita (so memoria) ----------------------------------------------
    def reservar(self, sala_id: str, inicio: str, fim: str, responsavel: str) -> Reserva:
        self.validar(sala_id, inicio, fim)
        reserva = Reserva(f"res-{self._proximo:04d}", sala_id, inicio, fim, responsavel)
        self._proximo += 1
        self.reservas.append(reserva)
        return reserva
