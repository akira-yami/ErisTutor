# -*- coding: utf-8 -*-
"""Small dependency-free chess rules and analysis engine.

It is intentionally modest: legal move validation, transition inference and a
shallow material/position search. A configured Stockfish process can replace it
later without changing the bridge contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Iterator

FILES = "abcdefgh"
RANKS = "12345678"
PIECE_VALUES = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 0}
MATE_SCORE = 100000


def square_name(index: int) -> str:
    return FILES[index % 8] + RANKS[index // 8]


def parse_square(value: str) -> int:
    text = str(value or "").lower()
    if len(text) != 2 or text[0] not in FILES or text[1] not in RANKS:
        raise ValueError(f"invalid square: {value}")
    return FILES.index(text[0]) + 8 * RANKS.index(text[1])


@dataclass(frozen=True, slots=True)
class Move:
    from_sq: int
    to_sq: int
    promotion: str = ""
    en_passant: bool = False
    castle: bool = False

    @property
    def uci(self) -> str:
        return square_name(self.from_sq) + square_name(self.to_sq) + self.promotion


@dataclass(frozen=True, slots=True)
class Position:
    board: tuple[str, ...]
    turn: str = "w"
    castling: str = "KQkq"
    ep_square: int | None = None
    halfmove: int = 0
    fullmove: int = 1

    @classmethod
    def initial(cls) -> "Position":
        return cls.from_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")

    @classmethod
    def from_fen(cls, fen: str) -> "Position":
        fields = str(fen or "").split()
        if not fields:
            raise ValueError("empty fen")
        rows = fields[0].split("/")
        if len(rows) != 8:
            raise ValueError("invalid board fen")
        board = ["."] * 64
        for visual_rank, row in enumerate(rows):
            rank = 7 - visual_rank
            file_index = 0
            for token in row:
                if token.isdigit():
                    file_index += int(token)
                elif token in "prnbqkPRNBQK":
                    if file_index >= 8:
                        raise ValueError("invalid board row")
                    board[rank * 8 + file_index] = token
                    file_index += 1
                else:
                    raise ValueError("invalid fen token")
            if file_index != 8:
                raise ValueError("invalid board width")
        turn = fields[1] if len(fields) > 1 and fields[1] in {"w", "b"} else "w"
        castling = fields[2] if len(fields) > 2 and fields[2] != "-" else ""
        ep = None
        if len(fields) > 3 and fields[3] != "-":
            ep = parse_square(fields[3])
        halfmove = int(fields[4]) if len(fields) > 4 else 0
        fullmove = int(fields[5]) if len(fields) > 5 else 1
        return cls(tuple(board), turn, castling, ep, halfmove, fullmove)

    @classmethod
    def from_board_fen(cls, board_fen: str, *, turn: str = "w", ply: int = 0) -> "Position":
        castling = guess_castling(board_fen)
        return cls.from_fen(
            f"{board_fen} {turn if turn in {'w','b'} else 'w'} {castling or '-'} - 0 {max(1, ply // 2 + 1)}"
        )

    @property
    def board_fen(self) -> str:
        rows: list[str] = []
        for rank in range(7, -1, -1):
            empty = 0
            row = ""
            for file_index in range(8):
                piece = self.board[rank * 8 + file_index]
                if piece == ".":
                    empty += 1
                else:
                    if empty:
                        row += str(empty)
                        empty = 0
                    row += piece
            if empty:
                row += str(empty)
            rows.append(row)
        return "/".join(rows)

    @property
    def fen(self) -> str:
        ep = square_name(self.ep_square) if self.ep_square is not None else "-"
        return f"{self.board_fen} {self.turn} {self.castling or '-'} {ep} {self.halfmove} {self.fullmove}"


def guess_castling(board_fen: str) -> str:
    position = Position.from_fen(f"{board_fen} w - - 0 1")
    board = position.board
    rights = ""
    if board[parse_square("e1")] == "K":
        if board[parse_square("h1")] == "R": rights += "K"
        if board[parse_square("a1")] == "R": rights += "Q"
    if board[parse_square("e8")] == "k":
        if board[parse_square("h8")] == "r": rights += "k"
        if board[parse_square("a8")] == "r": rights += "q"
    return rights


def color_of(piece: str) -> str:
    return "w" if piece.isupper() else "b"


def enemy(color: str) -> str:
    return "b" if color == "w" else "w"


def _inside(file_index: int, rank: int) -> bool:
    return 0 <= file_index < 8 and 0 <= rank < 8


def _ray_moves(position: Position, from_sq: int, directions: Iterable[tuple[int, int]]) -> Iterator[Move]:
    board = position.board
    color = color_of(board[from_sq])
    file_index, rank = from_sq % 8, from_sq // 8
    for df, dr in directions:
        nf, nr = file_index + df, rank + dr
        while _inside(nf, nr):
            target = nr * 8 + nf
            piece = board[target]
            if piece == ".":
                yield Move(from_sq, target)
            else:
                if color_of(piece) != color:
                    yield Move(from_sq, target)
                break
            nf += df
            nr += dr


def pseudo_legal_moves(position: Position) -> Iterator[Move]:
    board = position.board
    for from_sq, piece in enumerate(board):
        if piece == "." or color_of(piece) != position.turn:
            continue
        lower = piece.lower()
        file_index, rank = from_sq % 8, from_sq // 8
        if lower == "p":
            direction = 1 if position.turn == "w" else -1
            start_rank = 1 if position.turn == "w" else 6
            promotion_rank = 7 if position.turn == "w" else 0
            one_rank = rank + direction
            if _inside(file_index, one_rank):
                one = one_rank * 8 + file_index
                if board[one] == ".":
                    if one_rank == promotion_rank:
                        for promo in "qrbn": yield Move(from_sq, one, promo)
                    else:
                        yield Move(from_sq, one)
                    two_rank = rank + 2 * direction
                    if rank == start_rank:
                        two = two_rank * 8 + file_index
                        if board[two] == ".": yield Move(from_sq, two)
            for df in (-1, 1):
                nf, nr = file_index + df, rank + direction
                if not _inside(nf, nr): continue
                target = nr * 8 + nf
                captured = board[target]
                if captured != "." and color_of(captured) != position.turn:
                    if nr == promotion_rank:
                        for promo in "qrbn": yield Move(from_sq, target, promo)
                    else:
                        yield Move(from_sq, target)
                elif position.ep_square == target:
                    yield Move(from_sq, target, en_passant=True)
        elif lower == "n":
            for df, dr in ((1,2),(2,1),(2,-1),(1,-2),(-1,-2),(-2,-1),(-2,1),(-1,2)):
                nf, nr = file_index + df, rank + dr
                if not _inside(nf, nr): continue
                target = nr * 8 + nf
                if board[target] == "." or color_of(board[target]) != position.turn:
                    yield Move(from_sq, target)
        elif lower == "b":
            yield from _ray_moves(position, from_sq, ((1,1),(1,-1),(-1,1),(-1,-1)))
        elif lower == "r":
            yield from _ray_moves(position, from_sq, ((1,0),(-1,0),(0,1),(0,-1)))
        elif lower == "q":
            yield from _ray_moves(position, from_sq, ((1,1),(1,-1),(-1,1),(-1,-1),(1,0),(-1,0),(0,1),(0,-1)))
        elif lower == "k":
            for df in (-1,0,1):
                for dr in (-1,0,1):
                    if not (df or dr): continue
                    nf, nr = file_index + df, rank + dr
                    if not _inside(nf, nr): continue
                    target = nr * 8 + nf
                    if board[target] == "." or color_of(board[target]) != position.turn:
                        yield Move(from_sq, target)
            if position.turn == "w" and from_sq == parse_square("e1"):
                if "K" in position.castling and board[parse_square("f1")] == board[parse_square("g1")] == ".":
                    yield Move(from_sq, parse_square("g1"), castle=True)
                if "Q" in position.castling and board[parse_square("d1")] == board[parse_square("c1")] == board[parse_square("b1")] == ".":
                    yield Move(from_sq, parse_square("c1"), castle=True)
            if position.turn == "b" and from_sq == parse_square("e8"):
                if "k" in position.castling and board[parse_square("f8")] == board[parse_square("g8")] == ".":
                    yield Move(from_sq, parse_square("g8"), castle=True)
                if "q" in position.castling and board[parse_square("d8")] == board[parse_square("c8")] == board[parse_square("b8")] == ".":
                    yield Move(from_sq, parse_square("c8"), castle=True)


def attacked(position: Position, square: int, by_color: str) -> bool:
    board = position.board
    file_index, rank = square % 8, square // 8
    pawn_rank = rank - (1 if by_color == "w" else -1)
    for df in (-1, 1):
        nf = file_index + df
        if _inside(nf, pawn_rank):
            piece = board[pawn_rank * 8 + nf]
            if piece == ("P" if by_color == "w" else "p"):
                return True
    knight = "N" if by_color == "w" else "n"
    for df, dr in ((1,2),(2,1),(2,-1),(1,-2),(-1,-2),(-2,-1),(-2,1),(-1,2)):
        nf, nr = file_index + df, rank + dr
        if _inside(nf, nr) and board[nr * 8 + nf] == knight:
            return True
    king = "K" if by_color == "w" else "k"
    for df in (-1,0,1):
        for dr in (-1,0,1):
            if not (df or dr): continue
            nf, nr = file_index + df, rank + dr
            if _inside(nf, nr) and board[nr * 8 + nf] == king:
                return True
    for directions, pieces in (
        (((1,0),(-1,0),(0,1),(0,-1)), {"r", "q"}),
        (((1,1),(1,-1),(-1,1),(-1,-1)), {"b", "q"}),
    ):
        for df, dr in directions:
            nf, nr = file_index + df, rank + dr
            while _inside(nf, nr):
                piece = board[nr * 8 + nf]
                if piece != ".":
                    if color_of(piece) == by_color and piece.lower() in pieces:
                        return True
                    break
                nf += df; nr += dr
    return False


def in_check(position: Position, color: str | None = None) -> bool:
    color = color or position.turn
    king = "K" if color == "w" else "k"
    try:
        square = position.board.index(king)
    except ValueError:
        return True
    return attacked(position, square, enemy(color))


def apply_move(position: Position, move: Move) -> Position:
    board = list(position.board)
    piece = board[move.from_sq]
    captured = board[move.to_sq]
    board[move.from_sq] = "."
    if move.en_passant:
        capture_sq = move.to_sq - 8 if position.turn == "w" else move.to_sq + 8
        captured = board[capture_sq]
        board[capture_sq] = "."
    placed = piece
    if move.promotion:
        placed = move.promotion.upper() if position.turn == "w" else move.promotion.lower()
    board[move.to_sq] = placed
    if move.castle or (piece.lower() == "k" and abs(move.to_sq - move.from_sq) == 2):
        if move.to_sq > move.from_sq:
            rook_from, rook_to = move.from_sq + 3, move.from_sq + 1
        else:
            rook_from, rook_to = move.from_sq - 4, move.from_sq - 1
        board[rook_to] = board[rook_from]
        board[rook_from] = "."

    rights = position.castling
    for token, square in (("K", "e1"),("Q","e1"),("k","e8"),("q","e8")):
        if move.from_sq == parse_square(square): rights = rights.replace(token, "")
    for token, square in (("Q","a1"),("K","h1"),("q","a8"),("k","h8")):
        sq = parse_square(square)
        if move.from_sq == sq or move.to_sq == sq: rights = rights.replace(token, "")
    ep = None
    if piece.lower() == "p" and abs(move.to_sq - move.from_sq) == 16:
        ep = (move.to_sq + move.from_sq) // 2
    halfmove = 0 if piece.lower() == "p" or captured != "." else position.halfmove + 1
    fullmove = position.fullmove + (1 if position.turn == "b" else 0)
    return Position(tuple(board), enemy(position.turn), rights, ep, halfmove, fullmove)


def legal_moves(position: Position) -> tuple[Move, ...]:
    result: list[Move] = []
    for move in pseudo_legal_moves(position):
        if move.castle:
            if in_check(position, position.turn):
                continue
            step = move.from_sq + (1 if move.to_sq > move.from_sq else -1)
            if attacked(position, step, enemy(position.turn)) or attacked(position, move.to_sq, enemy(position.turn)):
                continue
        child = apply_move(position, move)
        if not in_check(child, position.turn):
            result.append(move)
    return tuple(result)


def move_san(position: Position, move: Move) -> str:
    piece = position.board[move.from_sq]
    if move.castle or (piece.lower() == "k" and abs(move.to_sq - move.from_sq) == 2):
        text = "O-O" if move.to_sq > move.from_sq else "O-O-O"
    else:
        captured = position.board[move.to_sq] != "." or move.en_passant
        if piece.lower() == "p":
            prefix = FILES[move.from_sq % 8] if captured else ""
        else:
            prefix = piece.upper()
        text = prefix + ("x" if captured else "") + square_name(move.to_sq)
        if move.promotion:
            text += "=" + move.promotion.upper()
    child = apply_move(position, move)
    if in_check(child, child.turn):
        text += "#" if not legal_moves(child) else "+"
    return text


def material_score(position: Position) -> int:
    total = 0
    for piece in position.board:
        if piece == ".": continue
        value = PIECE_VALUES[piece.lower()]
        total += value if piece.isupper() else -value
    return total


def evaluate(position: Position) -> int:
    moves = legal_moves(position)
    if not moves:
        if in_check(position):
            return -MATE_SCORE if position.turn == "w" else MATE_SCORE
        return 0
    score = material_score(position)
    center = {parse_square(sq) for sq in ("d4", "e4", "d5", "e5")}
    for sq, piece in enumerate(position.board):
        if piece == ".": continue
        bonus = 12 if sq in center and piece.lower() in {"n", "b", "p"} else 0
        score += bonus if piece.isupper() else -bonus
    mobility = len(moves)
    score += mobility if position.turn == "w" else -mobility
    return score


def _negamax(position: Position, depth: int, alpha: int, beta: int, perspective: str) -> int:
    moves = legal_moves(position)
    if depth <= 0 or not moves:
        score = evaluate(position)
        return score if perspective == "w" else -score
    best = -MATE_SCORE * 2
    for move in moves:
        value = -_negamax(apply_move(position, move), depth - 1, -beta, -alpha, perspective)
        if value > best: best = value
        if value > alpha: alpha = value
        if alpha >= beta: break
    return best


def analyze(position: Position, *, depth: int = 2, top_n: int = 3) -> tuple[tuple[Move, int], ...]:
    candidates: list[tuple[Move, int]] = []
    for move in legal_moves(position):
        child = apply_move(position, move)
        score = -_negamax(child, max(0, depth - 1), -MATE_SCORE * 2, MATE_SCORE * 2, position.turn)
        candidates.append((move, score))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return tuple(candidates[:max(1, top_n)])


def infer_transition(previous: Position, board_fen: str) -> tuple[Move | None, Position | None]:
    matches: list[tuple[Move, Position]] = []
    for move in legal_moves(previous):
        child = apply_move(previous, move)
        if child.board_fen == board_fen:
            matches.append((move, child))
    if len(matches) == 1:
        return matches[0]
    return None, None


def evaluation_label(score_cp: int) -> str:
    absolute = abs(int(score_cp))
    if absolute < 40: label = "balanced"
    elif absolute < 120: label = "slight"
    elif absolute < 300: label = "clear"
    elif absolute < 700: label = "winning"
    else: label = "decisive"
    side = "white" if score_cp > 0 else "black" if score_cp < 0 else "neither"
    return f"{label}_{side}"


__all__ = [
    "Move", "Position", "analyze", "apply_move", "evaluate", "evaluation_label",
    "infer_transition", "legal_moves", "move_san", "parse_square", "square_name",
]
