"""Offline pronunciation backends. Outputs are approximations, not translations.

Dependencies are lazy so script detection and same-language text need no extras.
Each instance owns its readers; Japanese conversion is serialized because the
reader keeps mutable state. Call warmup() outside a latency-sensitive loop.
"""

import re
import unicodedata
from threading import RLock

from core.tts.models import TTSLanguage


_VOWELS = {
    "AA": ("아", "a"), "AE": ("애", "a"), "AH": ("어", "a"),
    "AO": ("오", "o"), "AW": ("아우", "au"), "AY": ("아이", "ai"),
    "EH": ("에", "e"), "ER": ("어", "aa"), "EY": ("에이", "ei"),
    "IH": ("이", "i"), "IY": ("이", "ii"), "OW": ("오우", "oo"),
    "OY": ("오이", "oi"), "UH": ("우", "u"), "UW": ("우", "uu"),
}
_KO_ONSETS = {
    "B": 7, "CH": 14, "D": 3, "DH": 3, "F": 17, "G": 0,
    "HH": 18, "JH": 12, "K": 15, "L": 5, "M": 6, "N": 2,
    "NG": 11, "P": 17, "R": 5, "S": 9, "SH": 9, "T": 16,
    "TH": 9, "V": 7, "Z": 12, "ZH": 12,
}
_KO_CONSONANTS = {
    "B": "브", "CH": "치", "D": "드", "DH": "드", "F": "프",
    "G": "그", "HH": "흐", "JH": "지", "K": "크", "L": "르",
    "M": "므", "N": "느", "NG": "응", "P": "프", "R": "르",
    "S": "스", "SH": "쉬", "T": "트", "TH": "스", "V": "브",
    "W": "우", "Y": "이", "Z": "즈", "ZH": "지",
}
_JA_CONSONANTS = {
    "B": "ブ", "CH": "チ", "D": "ド", "DH": "ズ", "F": "フ",
    "G": "グ", "HH": "ハ", "JH": "ジ", "K": "ク", "L": "ル",
    "M": "ム", "N": "ン", "NG": "ン", "P": "プ", "R": "ル",
    "S": "ス", "SH": "シ", "T": "ト", "TH": "ス", "V": "ヴ",
    "W": "ウ", "Y": "イ", "Z": "ズ", "ZH": "ジ",
}
# Rows are a/i/u/e/o, including foreign-sound approximations.
_KANA_ROWS = {
    "": "ア イ ウ エ オ", "k": "カ キ ク ケ コ", "g": "ガ ギ グ ゲ ゴ",
    "s": "サ シ ス セ ソ", "z": "ザ ジ ズ ゼ ゾ", "t": "タ ティ トゥ テ ト",
    "d": "ダ ディ ドゥ デ ド", "n": "ナ ニ ヌ ネ ノ", "h": "ハ ヒ フ ヘ ホ",
    "b": "バ ビ ブ ベ ボ", "p": "パ ピ プ ペ ポ", "m": "マ ミ ム メ モ",
    "r": "ラ リ ル レ ロ", "y": "ヤ イ ユ イェ ヨ", "w": "ワ ウィ ウ ウェ ウォ",
    "sh": "シャ シ シュ シェ ショ", "ch": "チャ チ チュ チェ チョ",
    "j": "ジャ ジ ジュ ジェ ジョ", "f": "ファ フィ フ フェ フォ",
    "v": "ヴァ ヴィ ヴ ヴェ ヴォ",
}
_KANA_ROWS = {key: value.split() for key, value in _KANA_ROWS.items()}
_PHONE_ONSETS = {
    "B": "b", "CH": "ch", "D": "d", "DH": "z", "F": "f", "G": "g",
    "HH": "h", "JH": "j", "K": "k", "L": "r", "M": "m", "N": "n",
    "NG": "n", "P": "p", "R": "r", "S": "s", "SH": "sh", "T": "t",
    "TH": "s", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "j",
}
_LETTER_NAMES = {
    "ko": "에이 비 씨 디 이 에프 지 에이치 아이 제이 케이 엘 엠 엔 오 피 큐 알 에스 티 유 브이 더블유 엑스 와이 지".split(),
    "ja": "エー ビー シー ディー イー エフ ジー エイチ アイ ジェー ケー エル エム エヌ オー ピー キュー アール エス ティー ユー ヴィー ダブリュー エックス ワイ ズィー".split(),
}
_JAMO_NAMES = dict(zip(
    "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ",
    "기역 쌍기역 니은 디귿 쌍디귿 리을 미음 비읍 쌍비읍 시옷 쌍시옷 이응 지읒 쌍지읒 치읓 키읔 티읕 피읖 히읗".split(),
))
_JAMO_NAMES.update(zip(
    "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ",
    "아 애 야 얘 어 에 여 예 오 와 왜 외 요 우 워 웨 위 유 으 의 이".split(),
))
_RR_SYLLABLE = re.compile(
    r"(kk|tt|pp|ss|jj|ch|[gndrlmbsjkhtp]?)(yae|yeo|wae|ya|ye|yo|yu|wa|wo|oe|we|wi|ui|ae|eo|eu|[aeiou])"
)
_RR_VOWELS = {
    "a": ("a", ""), "ae": ("e", ""), "ya": ("a", "y"), "yae": ("e", "y"),
    "eo": ("o", ""), "e": ("e", ""), "yeo": ("o", "y"), "ye": ("e", "y"),
    "o": ("o", ""), "wa": ("a", "w"), "wae": ("e", "w"), "oe": ("e", "w"),
    "yo": ("o", "y"), "u": ("u", ""), "wo": ("o", "w"), "we": ("e", "w"),
    "wi": ("i", "w"), "yu": ("u", "y"), "eu": ("u", ""),
    "ui": ("i", "w"), "i": ("i", ""),
}
# Basic kana plus voiced rows. Extended combinations are matched longest first.
_KANA_KO = {}
for _kana, _hangul in (
    ("あいうえお", "아 이 우 에 오"), ("かきくけこ", "카 키 쿠 케 코"),
    ("がぎぐげご", "가 기 구 게 고"), ("さしすせそ", "사 시 스 세 소"),
    ("ざじずぜぞ", "자 지 즈 제 조"), ("たちつてと", "타 치 츠 테 토"),
    ("だぢづでど", "다 지 즈 데 도"), ("なにぬねの", "나 니 누 네 노"),
    ("はひふへほ", "하 히 후 헤 호"), ("ばびぶべぼ", "바 비 부 베 보"),
    ("ぱぴぷぺぽ", "파 피 푸 페 포"), ("まみむめも", "마 미 무 메 모"),
    ("やゆよ", "야 유 요"), ("らりるれろ", "라 리 루 레 로"),
    ("わゐゑを", "와 이 에 오"), ("ぁぃぅぇぉゃゅょゎゕゖゔ", "아 이 우 에 오 야 유 요 와 카 케 부"),
):
    _KANA_KO.update(zip(_kana, _hangul.split()))
for _kana, _hangul in (
    ("き", "캬 큐 쿄"), ("ぎ", "갸 규 교"), ("し", "샤 슈 쇼"),
    ("じ", "자 주 조"), ("ち", "차 추 초"), ("に", "냐 뉴 뇨"),
    ("ひ", "햐 휴 효"), ("び", "뱌 뷰 뵤"), ("ぴ", "퍄 퓨 표"),
    ("み", "먀 뮤 묘"), ("り", "랴 류 료"),
):
    _KANA_KO.update(zip((_kana + small for small in "ゃゅょ"), _hangul.split()))
_KANA_KO.update({
    "ふぁ": "파", "ふぃ": "피", "ふぇ": "페", "ふぉ": "포", "てぃ": "티",
    "でぃ": "디", "とぅ": "투", "どぅ": "두", "しぇ": "셰", "ちぇ": "체",
    "じぇ": "제", "うぃ": "위", "うぇ": "웨", "うぉ": "워", "いぇ": "예",
    "ゔぁ": "바", "ゔぃ": "비", "ゔぇ": "베", "ゔぉ": "보", "ゔゅ": "뷰",
})


def _attach_coda(parts: list[str], coda: int) -> bool:
    if parts and parts[-1]:
        code = ord(parts[-1][-1])
        if 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 == 0:
            parts[-1] = parts[-1][:-1] + chr(code + coda)
            return True
    return False


def _kana_syllable(onset: str, vowel: str, glide: str = "") -> str:
    index = "aiueo".index(vowel)
    if glide == "y" and onset not in ("", "y", "sh", "ch", "j"):
        return _KANA_ROWS[onset][1] + dict(a="ャ", i="", u="ュ", e="ェ", o="ョ")[vowel]
    if glide == "w" and onset not in ("", "w"):
        return _KANA_ROWS[onset][2] + dict(a="ァ", i="ィ", u="", e="ェ", o="ォ")[vowel]
    return _KANA_ROWS[onset or glide][index]


def _korean_to_kana(romanized: str) -> str:
    parts = []
    index = 0
    while index < len(romanized):
        match = _RR_SYLLABLE.match(romanized, index)
        if match:
            onset, vowel = match.groups()
            onset = {"kk": "k", "tt": "t", "pp": "p", "ss": "s", "jj": "j", "l": "r"}.get(onset, onset)
            base, glide = _RR_VOWELS[vowel]
            parts.append(_kana_syllable(onset, base, glide))
            index = match.end()
        elif romanized.startswith("ng", index):
            parts.append("ン")
            index += 2
        elif romanized[index] == "-":
            # koroman inserts disambiguating syllable boundaries (han-gugeo).
            index += 1
        else:
            parts.append({"n": "ン", "m": "ム", "l": "ル", "k": "ク", "t": "ト", "p": "プ"}.get(romanized[index], romanized[index]))
            index += 1
    return "".join(parts)


def _kana_to_korean(hiragana: str) -> str:
    parts = []
    index = 0
    while index < len(hiragana):
        char = hiragana[index]
        pair = hiragana[index:index + 2]
        if pair in _KANA_KO:
            parts.append(_KANA_KO[pair])
            index += 2
            continue
        if char in ("ん", "っ"):
            if not _attach_coda(parts, 4 if char == "ん" else 19):
                parts.append("은" if char == "ん" else "읏")
        elif char == "ー" and parts and 0xAC00 <= ord(parts[-1][-1]) <= 0xD7A3:
            vowel = (ord(parts[-1][-1]) - 0xAC00) // 28 % 21
            parts.append(chr(0xC544 + vowel * 28))
        else:
            parts.append(_KANA_KO.get(char, char))
        index += 1
    return "".join(parts)


def _english_phones(phones: list[str], target: TTSLanguage) -> str:
    phones = [phone.rstrip("012") for phone in phones]
    parts = []
    index = 0
    while index < len(phones):
        phone = phones[index]
        onset = ""
        glide = ""
        if phone not in _VOWELS:
            onset = phone
            index += 1
            if index + 1 < len(phones) and phones[index] in ("Y", "W") and phones[index + 1] in _VOWELS:
                glide = phones[index].lower()
                index += 1
            if index >= len(phones) or phones[index] not in _VOWELS:
                if target == "ko":
                    coda = {"N": 4, "M": 16, "NG": 21, "L": 8, "R": 8}.get(onset)
                    if not coda or not _attach_coda(parts, coda):
                        parts.append(_KO_CONSONANTS[onset])
                else:
                    parts.append(_JA_CONSONANTS[onset])
                continue
            phone = phones[index]
        korean, vowels = _VOWELS[phone]
        if target == "ko":
            if onset in ("W", "Y"):
                glide = onset.lower()
                onset = ""
            first = korean[0]
            if glide:
                first = {
                    "y": {"아": "야", "애": "얘", "어": "여", "오": "요", "우": "유", "에": "예"},
                    "w": {"아": "와", "애": "왜", "어": "워", "오": "워", "에": "웨", "이": "위"},
                }[glide].get(first, first)
            vowel_index = (ord(first) - 0xAC00) // 28 % 21
            parts.append(chr(0xAC00 + _KO_ONSETS.get(onset, 11) * 588 + vowel_index * 28) + korean[1:])
        else:
            kana = _kana_syllable(_PHONE_ONSETS.get(onset, ""), vowels[0], glide)
            if len(vowels) > 1:
                kana += "ー" if vowels[0] == vowels[1] else _kana_syllable("", vowels[1])
            parts.append(kana)
        index += 1
    return "".join(parts)


class PronunciationConverters:
    def __init__(self) -> None:
        self._lock = RLock()
        self._english = None
        self._japanese = None

    def _english_dictionary(self):
        with self._lock:
            if self._english is None:
                import cmudict

                self._english = cmudict.dict()
            return self._english

    def _japanese_reader(self):
        with self._lock:
            if self._japanese is None:
                from pykakasi import kakasi

                self._japanese = kakasi()
            return self._japanese

    def warmup(self) -> None:
        from koroman import romanize

        romanize("")
        self._english_dictionary()
        self._japanese_reader()

    def convert(self, text: str, source: str, target: TTSLanguage) -> str:
        if source == "ko":
            from koroman import romanize

            # Standalone chat consonants have no vowel; read their letter names.
            text = "".join(_JAMO_NAMES.get(char, char) for char in text)
            romanized = romanize(text, use_custom_dictionary=False, use_hyphen=target == "ja")
            return romanized if target == "en" else _korean_to_kana(romanized)
        if source == "ja":
            with self._lock:
                items = self._japanese_reader().convert(unicodedata.normalize("NFKC", text))
            if target == "en":
                return " ".join(item["hepburn"] for item in items)
            return _kana_to_korean("".join(item["hira"] for item in items))
        if source == "en":
            word = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
            word = word.replace("’", "'")
            if not all(char.isascii() and (char.isalpha() or char == "'") for char in word):
                return text
            if not (word.isupper() and len(word) > 1):
                pronunciations = self._english_dictionary().get(word.lower())
                if pronunciations:
                    return _english_phones(pronunciations[0], target)
            # Unknown identifiers and initialisms: pronounce letter names.
            names = _LETTER_NAMES[target]
            return "".join(names[ord(char) - ord("a")] for char in word.lower() if char.isalpha())
        return text
