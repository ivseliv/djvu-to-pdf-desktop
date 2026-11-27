"""Read the parenthesized text zones and bookmarks emitted by djvused."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class Expression:
    @property
    def value(self) -> Any:  # ruff: ignore[any-type]
        raise NotImplementedError

    @staticmethod
    def from_string(text: str | bytes) -> ListExpression:
        return parse(text.encode('utf-8') if isinstance(text, str) else text)


class ListExpression(list[Expression], Expression):
    @property
    def value(self) -> list[Any]:
        return [item.value for item in self]


Symbol = str


@dataclass(frozen=True, slots=True)
class SymbolExpression(Expression):
    name: str

    @property
    def value(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class IntExpression(Expression):
    number: int

    @property
    def value(self) -> int:
        return self.number


@dataclass(frozen=True, slots=True)
class StringExpression(Expression):
    data: bytes

    @property
    def value(self) -> str:
        return self.data.decode('utf-8')

    def __str__(self) -> str:
        escaped = ''.join(
            chr(c) if 32 <= c <= 126 and c not in {34, 92} else f'\\{c:03o}'
            for c in self.data
        )
        return f'"{escaped}"'


_TOKEN = re.compile(rb'\s+|[()]|"(?:[^"\\]|\\.)*"|[^()\s"]+', re.DOTALL)
_ESCAPE = re.compile(rb'\\([0-7]{1,3}|x[0-9A-Fa-f]{2}|.)', re.DOTALL)
_CONTROLS = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'f': b'\f', b'b': b'\b', b'v': b'\v', b'a': b'\a'}


def _unescape(match: re.Match[bytes]) -> bytes:
    code = match[1]
    if 48 <= code[0] <= 55:
        return bytes([int(code, 8)])
    if len(code) == 3 and code.startswith(b'x'):
        return bytes([int(code[1:], 16)])
    return _CONTROLS.get(code, code)


def parse(data: bytes) -> ListExpression:
    """Parse one list without decoding or dropping invalid UTF-8 in its neighbours."""
    if not data.strip():
        return ListExpression()
    return _parse_list(data)


def _parse_list(data: bytes) -> ListExpression:
    root = ListExpression()
    stack = [root]
    end = 0
    for match in _TOKEN.finditer(data):
        if match.start() != end:
            raise ValueError('Unterminated quoted string in DjVu metadata.')
        end = match.end()
        token = match[0]
        if token.isspace():
            continue
        if token == b'(':
            child = ListExpression()
            stack[-1].append(child)
            stack.append(child)
        elif token == b')':
            if len(stack) == 1:
                raise ValueError('Unexpected closing parenthesis in DjVu metadata.')
            stack.pop()
        elif token.startswith(b'"'):
            content = token[1:-1]
            stack[-1].append(StringExpression(_ESCAPE.sub(_unescape, content) if b'\\' in content else content))
        elif token.lstrip(b'-').isdigit():
            stack[-1].append(IntExpression(int(token)))
        else:
            stack[-1].append(SymbolExpression(token.decode('ascii')))
    if end != len(data) or len(stack) != 1 or len(root) != 1 or not isinstance(root[0], ListExpression):
        raise ValueError('Incomplete or unexpected DjVu metadata.')
    return root[0]
