# cseq

Cソースツリーから関数呼出し・分岐・コールバック・実行時markerを解析し、PlantUMLとインタラクティブHTMLを生成するシーケンス解析ツールです。

cseqは **Python 3.8以上** を対応下限として固定します。現在の明示検証対象は Python 3.8 / 3.9 / 3.10 / 3.11 / 3.12 / 3.13 / 3.14 / 3.15 です。新しいPythonが追加されても、それを理由に3.8以降の既存対応を順次打ち切る運用にはしません。Tree-sitterを高速な構文フロントエンドとして使用し、型・macro・関数ポインタ等で意味解析が必要な翻訳単位だけClangへ送ります。


### Python互換性

- Python 3.8 / 3.9では、prebuilt wheelが利用できる互換Tree-sitter profileへ自動分岐します。
- Python 3.10以上では現行Tree-sitter profileを使用します。
- 新しいPythonを追加したことを理由に既存の古い対応版を自動で削除しません。
- 新版Pythonは実import・ABI・parse smokeで受入可否を判定し、Python番号allowlistだけで拒否しません。
- 古いPython向けupstream wheelが将来消える場合も、cseq側で既知良好wheel/binaryを保持する方針を優先します。

## 主な機能

- ディレクトリ以下の `.c` / `.h` を再帰走査
- entry関数からのシーケンス生成
- 同名`static`関数を区別
- 外部未定義関数を内部calleeとして誤認しない
- `#if/#ifdef`、macro、compiler extensionのfail-soft解析
- 関数ポインタ、callback、dispatch tableの候補解決
- if/else、loop、無限loop、switch/case/defaultをSequence IRへ保持
- RTOS風の無限loopを有限表現
- CPU group / build configurationを外部設定可能
- runtime markerログの読込み、CPU/task evidence、静的経路との照合
- marker自動注入: source直接書換え / Overlay（原本非変更）の両方式
- PlantUML出力
- HTML UI: 検索、viewport、LOD、Canvas表示、path query、trace diff、marker suggestion
- 大きいruntime trace向けbundle/range出力
- optional linker map / binary symbol / DWARF evidence

## Windowsでの導入

PowerShell例です。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install "cseq[parser-tree-sitter]"
```

ローカルwheelから導入する場合:

```powershell
pip install .\cseq-0.0.2-py3-none-any.whl[parser-tree-sitter]
```

### Clang

単純なC構文だけならTree-sitterで解析できますが、macro、関数ポインタ、型曖昧性、compiler extension等のselective semantic refinementにはClangを推奨します。

```powershell
winget install --id LLVM.LLVM --exact
```

確認:

```powershell
cseq doctor
```

`current_execution_mode` が `tree-sitter+clang` なら両backendを利用できます。

## 最短利用例

```powershell
cseq analyze C:\path\to\project --entry main --json
cseq plantuml C:\path\to\project --entry main --output sequence.puml
cseq html C:\path\to\project --entry main --output sequence.html --mode auto
```

HTMLは小規模時はportable単一HTML、中規模以上ではbundle/rangeへ切替できます。

```powershell
cseq serve . --port 8000
```

range/bundleをブラウザで確認する際はローカルHTTP配信を推奨します。

## プロジェクト設定

```powershell
cseq config init C:\path\to\project
cseq config validate C:\path\to\project\cseq.toml
```

`cseq.toml`でentry、compile database、CPU group、build configuration等をプロジェクト固有名に合わせて設定します。cseq本体は特定企業・CPU名称を前提にしません。

`compile_commands.json` がある場合はClang semantic解析の精度向上に利用できます。

## Runtime marker

### 直接書換え

```powershell
cseq marker apply .\src\worker.c --contains "rx=%d" --id RX01 --manifest .\rx01.json
```

manifestにbefore/after hashを保存し、安全条件が成立するときだけundoできます。

```powershell
cseq marker undo .\src\worker.c --manifest .\rx01.json
```

### Overlay（原本非変更）

```powershell
cseq marker apply .\src\worker.c `
  --contains "rx=%d" `
  --id RX01 `
  --manifest .\rx01-overlay.json `
  --overlay-output .\cseq-overlay\worker.c
```

元sourceは変更されず、marker注入済みコピーだけを生成します。

## Runtime log

marker形式:

```text
[[CSEQ:RX01]] message...
```

ログ形式は固定ではなくpatternで指定できます。

```powershell
cseq trace import run.log `
  --pattern "[{cpu}] {time} {message}" `
  --time-format "%H:%M:%S.%f" `
  --json
```

CPU間で時刻が隣接しているだけでは因果関係とはみなしません。marker、payload、静的path等のevidenceを分離して扱います。

## HTML UI

```powershell
cseq html C:\path\to\project `
  --entry main `
  --runtime-log run.log `
  --runtime-pattern "[{cpu}] {time} {message}" `
  --runtime-time-format "%H:%M:%S.%f" `
  --output sequence.html `
  --mode auto
```

UIは大量eventを全DOM化せず、viewport/LOD/Canvas/range bundleを使います。初回リリースでは深い性能チューニングより、汎用性・正しさ・必要UIを優先します。

## 設計上の原則

- Silent Dropしない。解析不能領域はPARTIAL/OPAQUE等で明示する。
- static possible と runtime observed を混同しない。
- CPUのstatic/build/runtime evidenceを分離する。
- cross-CPU timestamp adjacencyだけで因果を断定しない。
- Tree-sitterとClangは代替関係ではなく、Fast Syntax + Selective Semantic Refinementとして併用する。

## 開発時検証

```powershell
python -m pytest -q
```

Windows実機で全テスト、Tree-sitter/Clang統合acceptance、clean wheel installを確認してからrelease候補を作成します。

## 同梱ドキュメントの出力

インストール済みwheelから、README・設計書（清書版）・実装/検証成績書を1コマンドで取り出せます。

```powershell
cseq docs readme --output README.md
cseq docs design --output シーケンス解析_設計書.md
cseq docs report --output 実装・検証成績書.html
cseq docs all --output-dir .\cseq-docs
```

`--output`を省略するとREADME/設計書/成績書の内容を標準出力します。`docs all` は3点を一括で出力します。

