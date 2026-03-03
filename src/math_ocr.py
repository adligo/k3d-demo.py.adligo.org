#!/usr/bin/env python3
"""PyTorch model that maps handwritten math formula segments to LaTeX.

Architecture
------------
- **Recursive Segmentation**: Each input image is recursively split into
  bounded contexts — a tree of regions where ink strokes are self-contained.
- **Encoder**: A small CNN converts each leaf segment image into a grid of
  feature vectors.
- **Decoder**: A GRU with Bahdanau (additive) attention autoregressively
  generates the LaTeX token sequence from the encoded features.
- **Additive Synthesis**: Bottom-up reconstruction combines leaf predictions
  into the full LaTeX formula.

The training set is derived from recursively segmenting the 11 image/LaTeX
pairs in ``src/math-images/`` and ``src/tex/``.  Each leaf segment is paired
with the corresponding sub-expression from the LaTeX formula.

Usage
-----
    # Install dependencies via uv
    uv sync

    # Segment images, train the model, and run inference
    uv run python src/math_ocr.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from src.segmentation import segment_image, crop_contexts
from src.label_alignment import split_latex_top_level, align_segments_to_labels
from src.segment_tree import SegmentNode, recursive_segment, save_tree
from src.synthesis import synthesize_latex, write_processing_files

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = PROJECT_ROOT / "src" / "math-images"
TEX_DIR = PROJECT_ROOT / "src" / "tex"
OUT_DIR = PROJECT_ROOT / "out"
PROCESSING_DIR = PROJECT_ROOT / "processing"
MODEL_PATH = PROJECT_ROOT / "math_ocr_model.pt"

# ---------------------------------------------------------------------------
# Tokeniser — a simple character-level tokeniser over LaTeX math strings
# ---------------------------------------------------------------------------

# Special tokens
PAD_TOKEN = "<PAD>"
SOS_TOKEN = "<SOS>"
EOS_TOKEN = "<EOS>"
UNK_TOKEN = "<UNK>"
SPECIAL_TOKENS = [PAD_TOKEN, SOS_TOKEN, EOS_TOKEN, UNK_TOKEN]


def _extract_math(tex_source: str) -> str:
    """Pull the equation body out of a .tex file.

    Looks for content between ``\\begin{equation*}`` and ``\\end{equation*}``,
    strips it, and returns the raw LaTeX math string.
    """
    match = re.search(
        r"\\begin\{equation\*\}\s*(.*?)\s*\\end\{equation\*\}",
        tex_source,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    # Fallback: return everything between \begin{document} and \end{document}
    match = re.search(
        r"\\begin\{document\}\s*(.*?)\s*\\end\{document\}",
        tex_source,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return tex_source.strip()


class Tokeniser:
    """Character-level tokeniser with a fixed vocabulary built from data."""

    def __init__(self) -> None:
        self.char2idx: dict[str, int] = {}
        self.idx2char: dict[int, str] = {}

    def build_vocab(self, texts: list[str]) -> None:
        chars: set[str] = set()
        for t in texts:
            chars.update(t)
        vocab = SPECIAL_TOKENS + sorted(chars)
        self.char2idx = {c: i for i, c in enumerate(vocab)}
        self.idx2char = {i: c for c, i in self.char2idx.items()}

    @property
    def vocab_size(self) -> int:
        return len(self.char2idx)

    @property
    def pad_idx(self) -> int:
        return self.char2idx[PAD_TOKEN]

    @property
    def sos_idx(self) -> int:
        return self.char2idx[SOS_TOKEN]

    @property
    def eos_idx(self) -> int:
        return self.char2idx[EOS_TOKEN]

    def encode(self, text: str) -> list[int]:
        unk = self.char2idx[UNK_TOKEN]
        return (
            [self.sos_idx]
            + [self.char2idx.get(c, unk) for c in text]
            + [self.eos_idx]
        )

    def decode(self, indices: list[int]) -> str:
        tokens: list[str] = []
        for i in indices:
            ch = self.idx2char.get(i, UNK_TOKEN)
            if ch == EOS_TOKEN:
                break
            if ch in (PAD_TOKEN, SOS_TOKEN):
                continue
            tokens.append(ch)
        return "".join(tokens)


# ---------------------------------------------------------------------------
# Phase 1: Recursive Segmentation
# ---------------------------------------------------------------------------

# Segment image dimensions (smaller than full-formula images)
SEG_HEIGHT = 64
SEG_WIDTH = 128

seg_transform = transforms.Compose([
    transforms.Grayscale(num_output_channels=1),
    transforms.Resize((SEG_HEIGHT, SEG_WIDTH)),
    transforms.ToTensor(),              # [0, 1]
    transforms.Normalize([0.5], [0.5]), # [-1, 1]
])


def _load_image_latex_pairs() -> list[tuple[Path, str]]:
    """Load image paths paired with their extracted LaTeX, skipping empties."""
    pairs: list[tuple[Path, str]] = []
    for img_path in sorted(IMAGE_DIR.glob("*.png")):
        tex_path = TEX_DIR / img_path.with_suffix(".tex").name
        if not tex_path.exists():
            print(f"  WARNING: No .tex file for {img_path.name}, skipping.")
            continue
        tex_content = tex_path.read_text().strip()
        if not tex_content:
            print(f"  WARNING: Empty .tex file for {img_path.name}, skipping.")
            continue
        math_body = _extract_math(tex_content)
        if not math_body:
            print(f"  WARNING: No math found in {tex_path.name}, skipping.")
            continue
        pairs.append((img_path, math_body))
    return pairs


def run_recursive_segmentation(
    pairs: list[tuple[Path, str]],
) -> tuple[list[tuple[Image.Image, str, str]], dict[str, tuple[SegmentNode, str]]]:
    """Recursively segment all images and build trees.

    Saves tree structure to nested ``out/<image_stem>/...`` directories.

    Returns:
      - A flat list of (leaf_PIL_image, latex_label, leaf_name) for training
      - A dict mapping formula_name → (tree, golden_latex) for synthesis
    """
    OUT_DIR.mkdir(exist_ok=True)
    all_leaves: list[tuple[Image.Image, str, str]] = []
    formula_trees: dict[str, tuple[SegmentNode, str]] = {}

    for img_path, full_latex in pairs:
        stem = img_path.stem
        out_subdir = OUT_DIR / stem

        image = Image.open(img_path).convert("RGB")

        # Build recursive tree
        tree = recursive_segment(image, full_latex, depth=0, parent_id="root")

        # Save tree to disk
        save_tree(tree, out_subdir)

        # Collect leaf segments for training
        leaves = tree.leaves()
        leaf_count = len(leaves)
        max_d = tree.max_depth()

        print(f"  {stem}: {leaf_count} leaves, max depth {max_d}")

        for leaf in leaves:
            leaf_name = f"{stem}/{leaf.segment_id}"
            all_leaves.append((leaf.image, leaf.latex_label, leaf_name))

        formula_trees[stem] = (tree, full_latex)

    return all_leaves, formula_trees


# ---------------------------------------------------------------------------
# Phase 2: Training — SegmentDataset + Model
# ---------------------------------------------------------------------------

class SegmentDataset(Dataset):
    """Dataset of (cropped_segment_image, latex_label) pairs."""

    def __init__(
        self,
        segments: list[tuple[Image.Image, str, str]],
        tokeniser: Tokeniser,
        max_len: int = 128,
    ) -> None:
        self.samples: list[tuple[Image.Image, str, str]] = segments
        self.tokeniser = tokeniser
        self.max_len = max_len

        # Build vocabulary from all labels
        all_labels = [lbl for _, lbl, _ in segments]
        tokeniser.build_vocab(all_labels)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        crop, label_text, _ = self.samples[idx]
        image = seg_transform(crop)

        token_ids = self.tokeniser.encode(label_text)
        if len(token_ids) < self.max_len:
            token_ids += [self.tokeniser.pad_idx] * (self.max_len - len(token_ids))
        else:
            token_ids = token_ids[: self.max_len - 1] + [self.tokeniser.eos_idx]
        return image, torch.tensor(token_ids, dtype=torch.long)


# ---------------------------------------------------------------------------
# Model — Encoder
# ---------------------------------------------------------------------------

class CNNEncoder(nn.Module):
    """Small CNN that produces a sequence of feature vectors from an image.

    Output shape: ``(batch, seq_len, hidden_dim)`` where *seq_len* is
    determined by the spatial dimensions after convolution.
    """

    def __init__(self, hidden_dim: int = 128) -> None:
        super().__init__()
        self.cnn = nn.Sequential(
            # Block 1
            nn.Conv2d(1, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2, 2),
            # Block 2
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2, 2),
            # Block 3
            nn.Conv2d(128, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2, 2),
            # Block 4
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.MaxPool2d((2, 1), (2, 1)),  # collapse height faster
        )
        self.proj = nn.Linear(256, hidden_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, 1, H, W)
        features = self.cnn(x)                    # (B, 256, H', W')
        b, c, h, w = features.shape
        features = features.permute(0, 2, 3, 1)   # (B, H', W', C)
        features = features.reshape(b, h * w, c)  # (B, seq, C)
        features = self.proj(features)             # (B, seq, hidden)
        return features


# ---------------------------------------------------------------------------
# Model — Attention
# ---------------------------------------------------------------------------

class BahdanauAttention(nn.Module):
    """Additive (Bahdanau) attention."""

    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.W_enc = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.W_dec = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.v = nn.Linear(hidden_dim, 1, bias=False)

    def forward(
        self, decoder_hidden: torch.Tensor, encoder_outputs: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        # decoder_hidden: (B, hidden)
        # encoder_outputs: (B, seq, hidden)
        hidden_expanded = decoder_hidden.unsqueeze(1)  # (B, 1, hidden)
        energy = torch.tanh(
            self.W_enc(encoder_outputs) + self.W_dec(hidden_expanded)
        )
        scores = self.v(energy).squeeze(-1)            # (B, seq)
        weights = F.softmax(scores, dim=-1)            # (B, seq)
        context = torch.bmm(weights.unsqueeze(1), encoder_outputs)  # (B, 1, h)
        context = context.squeeze(1)                   # (B, hidden)
        return context, weights


# ---------------------------------------------------------------------------
# Model — Decoder
# ---------------------------------------------------------------------------

class AttentionDecoder(nn.Module):
    """GRU decoder with Bahdanau attention."""

    def __init__(
        self, vocab_size: int, embed_dim: int, hidden_dim: int, pad_idx: int
    ) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=pad_idx)
        self.attention = BahdanauAttention(hidden_dim)
        self.gru = nn.GRU(embed_dim + hidden_dim, hidden_dim, batch_first=True)
        self.fc_out = nn.Linear(hidden_dim, vocab_size)

    def forward(
        self,
        input_token: torch.Tensor,
        hidden: torch.Tensor,
        encoder_outputs: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        # input_token: (B,)  hidden: (1, B, hidden)
        embedded = self.embedding(input_token)           # (B, embed)
        context, _ = self.attention(hidden.squeeze(0), encoder_outputs)
        gru_input = torch.cat([embedded, context], dim=-1).unsqueeze(1)
        output, hidden = self.gru(gru_input, hidden)     # output: (B, 1, h)
        logits = self.fc_out(output.squeeze(1))          # (B, vocab)
        return logits, hidden


# ---------------------------------------------------------------------------
# Model — Seq2Seq wrapper
# ---------------------------------------------------------------------------

class MathOCRModel(nn.Module):
    """Full encoder-decoder model for image → LaTeX."""

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 64,
        hidden_dim: int = 128,
        pad_idx: int = 0,
    ) -> None:
        super().__init__()
        self.encoder = CNNEncoder(hidden_dim)
        self.decoder = AttentionDecoder(vocab_size, embed_dim, hidden_dim, pad_idx)
        self.hidden_dim = hidden_dim
        # Learnable initial hidden state projection
        self.init_hidden = nn.Linear(hidden_dim, hidden_dim)

    def _encoder_to_hidden(self, encoder_outputs: torch.Tensor) -> torch.Tensor:
        """Derive the initial decoder hidden state from encoder outputs."""
        mean_enc = encoder_outputs.mean(dim=1)                # (B, hidden)
        h0 = torch.tanh(self.init_hidden(mean_enc)).unsqueeze(0)  # (1, B, h)
        return h0

    def forward(
        self,
        images: torch.Tensor,
        targets: torch.Tensor,
        teacher_forcing_ratio: float = 0.5,
    ) -> torch.Tensor:
        """Forward pass with optional teacher forcing.

        Returns logits of shape ``(B, max_len-1, vocab_size)`` — we skip the
        first ``<SOS>`` position of the target.
        """
        batch_size = images.size(0)
        max_len = targets.size(1)
        vocab_size = self.decoder.fc_out.out_features

        encoder_outputs = self.encoder(images)
        hidden = self._encoder_to_hidden(encoder_outputs)

        # First decoder input is always <SOS>
        input_token = targets[:, 0]  # (B,)

        all_logits = torch.zeros(batch_size, max_len - 1, vocab_size,
                                 device=images.device)

        for t in range(1, max_len):
            logits, hidden = self.decoder(input_token, hidden, encoder_outputs)
            all_logits[:, t - 1] = logits

            # Teacher forcing
            if torch.rand(1).item() < teacher_forcing_ratio:
                input_token = targets[:, t]
            else:
                input_token = logits.argmax(dim=-1)

        return all_logits

    @torch.no_grad()
    def predict(
        self,
        image: torch.Tensor,
        sos_idx: int,
        eos_idx: int,
        max_len: int = 128,
    ) -> list[int]:
        """Greedy decode a single image.

        *image* should be a ``(1, 1, H, W)`` tensor.
        """
        self.eval()
        encoder_outputs = self.encoder(image)
        hidden = self._encoder_to_hidden(encoder_outputs)

        input_token = torch.tensor([sos_idx], device=image.device)
        result: list[int] = []

        for _ in range(max_len):
            logits, hidden = self.decoder(input_token, hidden, encoder_outputs)
            next_token = logits.argmax(dim=-1)
            tok_id = next_token.item()
            if tok_id == eos_idx:
                break
            result.append(tok_id)
            input_token = next_token

        return result


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(
    model: MathOCRModel,
    dataset: SegmentDataset,
    tokeniser: Tokeniser,
    *,
    epochs: int = 600,
    lr: float = 3e-4,
    device: str = "cpu",
) -> None:
    """Train the model to memorise the segment dataset."""
    loader = DataLoader(dataset, batch_size=len(dataset), shuffle=True)
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss(ignore_index=tokeniser.pad_idx)
    model.to(device)
    model.train()

    for epoch in range(1, epochs + 1):
        for images, targets in loader:
            images = images.to(device)
            targets = targets.to(device)

            # Teacher-forcing ratio: start high, anneal to 0
            tf_ratio = max(0.0, 1.0 - epoch / (epochs * 0.8))

            logits = model(images, targets, teacher_forcing_ratio=tf_ratio)
            # logits: (B, max_len-1, vocab)  targets shifted by 1
            loss = criterion(
                logits.reshape(-1, logits.size(-1)),
                targets[:, 1:].reshape(-1),
            )

            optimiser.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimiser.step()

        if epoch % 50 == 0 or epoch == 1:
            print(f"  Epoch {epoch:4d}/{epochs}  loss={loss.item():.4f}  "
                  f"tf_ratio={tf_ratio:.2f}")

    print("  Training complete.")


# ---------------------------------------------------------------------------
# Phase 3: Inference on Leaves
# ---------------------------------------------------------------------------

def evaluate(
    model: MathOCRModel,
    dataset: SegmentDataset,
    tokeniser: Tokeniser,
    device: str = "cpu",
) -> dict[str, str]:
    """Run greedy inference on every leaf segment and print results.

    Returns a dict mapping leaf_name → predicted_latex for use in synthesis.
    """
    model.to(device)
    model.eval()

    correct = 0
    total = len(dataset)
    predictions: dict[str, str] = {}

    print(f"\n{'Leaf':<35s} {'Match':>5s}  Predicted LaTeX")
    print("-" * 80)

    for idx in range(total):
        crop, expected_text, leaf_name = dataset.samples[idx]
        image, _ = dataset[idx]
        image = image.unsqueeze(0).to(device)

        predicted_ids = model.predict(
            image, tokeniser.sos_idx, tokeniser.eos_idx
        )
        predicted_text = tokeniser.decode(predicted_ids)

        match = predicted_text.strip() == expected_text.strip()
        if match:
            correct += 1

        status = "OK" if match else "MISS"
        print(f"  {leaf_name:<33s} [{status:>4s}]  "
              f"{predicted_text!r}  (expected {expected_text!r})")

        predictions[leaf_name] = predicted_text

    print(f"\nLeaf accuracy: {correct}/{total} "
          f"({100 * correct / total:.0f}%)")

    return predictions


# ---------------------------------------------------------------------------
# Phase 4: Additive Synthesis
# ---------------------------------------------------------------------------

def run_synthesis(
    formula_trees: dict[str, tuple[SegmentNode, str]],
    predictions: dict[str, str],
    device: str = "cpu",
) -> None:
    """Run additive synthesis: combine leaf predictions into full formulas.

    For each formula tree:
    1. Set predicted_latex on every leaf from model predictions
    2. Synthesize the full LaTeX bottom-up
    3. Write results to processing/<name>.json
    4. Print comparison vs golden answer
    """
    PROCESSING_DIR.mkdir(exist_ok=True)

    correct = 0
    total = len(formula_trees)

    print(f"\n{'Formula':<25s} {'Match':>5s}  Synthesized LaTeX")
    print("-" * 80)

    for stem, (tree, golden_latex) in sorted(formula_trees.items()):
        # Set predictions on leaves
        for leaf in tree.leaves():
            leaf_name = f"{stem}/{leaf.segment_id}"
            leaf.predicted_latex = predictions.get(leaf_name, "")

        # Write processing file and get result
        result = write_processing_files(stem, tree, golden_latex, PROCESSING_DIR)

        synthesized = result["synthesized_latex"]
        matches = result["matches_golden"]
        if matches:
            correct += 1

        status = "OK" if matches else "MISS"
        print(f"  {stem:<23s} [{status:>4s}]  {synthesized!r}")
        print(f"  {'':23s}         (golden: {golden_latex!r})")

    print(f"\nFormula-level accuracy: {correct}/{total} "
          f"({100 * correct / total:.0f}%)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}\n")

    # --- Phase 1: Recursive Segmentation ---
    print("Phase 1: Recursively segmenting images into bounded contexts...")
    pairs = _load_image_latex_pairs()
    print(f"  Found {len(pairs)} image/LaTeX pairs (skipping empty .tex files).\n")

    if not pairs:
        print("ERROR: No image/LaTeX pairs found. Check paths.")
        sys.exit(1)

    leaves, formula_trees = run_recursive_segmentation(pairs)
    print(f"\n  Total leaf segments: {len(leaves)}\n")

    # --- Phase 2: Training ---
    print("Phase 2: Training on leaf segments...")
    tokeniser = Tokeniser()
    dataset = SegmentDataset(leaves, tokeniser)
    print(f"  Vocabulary size: {tokeniser.vocab_size} characters.")
    print(f"  Training samples: {len(dataset)}\n")

    model = MathOCRModel(
        vocab_size=tokeniser.vocab_size,
        embed_dim=64,
        hidden_dim=128,
        pad_idx=tokeniser.pad_idx,
    )
    total_params = sum(p.numel() for p in model.parameters())
    print(f"  Model parameters: {total_params:,}\n")

    train(model, dataset, tokeniser, epochs=600, lr=3e-4, device=device)

    # Save model
    torch.save({
        "model_state": model.state_dict(),
        "tokeniser_char2idx": tokeniser.char2idx,
        "tokeniser_idx2char": tokeniser.idx2char,
    }, MODEL_PATH)
    print(f"\n  Model saved to {MODEL_PATH}\n")

    # --- Phase 3: Inference on Leaves ---
    print("Phase 3: Running inference on all leaf segments...")
    predictions = evaluate(model, dataset, tokeniser, device=device)

    # --- Phase 4: Additive Synthesis ---
    print("\nPhase 4: Additive synthesis — reconstructing full formulas...")
    run_synthesis(formula_trees, predictions, device=device)


if __name__ == "__main__":
    main()
