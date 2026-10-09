"""Pareia os três ícones de cima com os três de baixo usando embeddings ResNet.

Uso: python "teste captcha.py" caminho/para/captcha.png
As coordenadas em ``extract_slots`` são relativas à imagem e podem ser ajustadas
para o layout específico do captcha.
"""

import argparse
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as T
from scipy.optimize import linear_sum_assignment
from PIL import Image


# Rotacionar 90 graus cobre as orientações comuns sem busca exaustiva de ângulos.
ROTATIONS = (0, 90, 180, 270)


@lru_cache(maxsize=1)
def _load_feature_extractor():
    """Carrega ResNet18 pré-treinada uma única vez (baixa os pesos na primeira execução)."""
    try:
        weights = models.ResNet18_Weights.DEFAULT
        model = models.resnet18(weights=weights)
        preprocess = weights.transforms()
    except AttributeError:  # compatibilidade com versões antigas do torchvision
        model = models.resnet18(pretrained=True)
        preprocess = T.Compose([
            T.Resize(256),
            T.CenterCrop(224),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    extractor = torch.nn.Sequential(*list(model.children())[:-1]).to(device).eval()
    return extractor, preprocess, device


def _rotate_bound(image, angle):
    """Gira sem cortar os cantos e preenche o fundo com a cor média da imagem."""
    if angle == 0:
        return image
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos_a, sin_a = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(h * sin_a + w * cos_a)
    new_h = int(h * cos_a + w * sin_a)
    matrix[0, 2] += new_w / 2 - center[0]
    matrix[1, 2] += new_h / 2 - center[1]
    fill = tuple(int(v) for v in image.reshape(-1, 3).mean(axis=0))
    return cv2.warpAffine(image, matrix, (new_w, new_h), borderValue=fill)


def get_image_embedding(crop_img):
    """Cria embedding robusto a rotação e reduz a influência da paleta de cores."""
    if crop_img is None or crop_img.size == 0:
        raise ValueError("Um dos recortes está vazio; confira as coordenadas dos slots.")

    extractor, preprocess, device = _load_feature_extractor()
    # A versão em cinza ajuda quando brilho ou paleta variam muito; a original
    # mantém informação útil quando as cores ainda são consistentes.
    variants = []
    gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
    gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for base in (crop_img, gray_bgr):
        for angle in ROTATIONS:
            rotated = _rotate_bound(base, angle)
            rgb = cv2.cvtColor(rotated, cv2.COLOR_BGR2RGB)
            variants.append(preprocess(Image.fromarray(rgb)))

    batch = torch.stack(variants).to(device)
    with torch.inference_mode():
        embeddings = extractor(batch).flatten(1)
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
        # Média dos descritores de cada orientação, com normalização final.
        result = torch.nn.functional.normalize(embeddings.mean(dim=0), p=2, dim=0)
    return result.cpu().numpy()


def extract_slots(captcha_img):
    """Recorta os seis slots por coordenadas relativas à captura."""
    if captcha_img is None or captcha_img.ndim != 3:
        raise ValueError("A captura não é uma imagem válida.")
    h, w = captcha_img.shape[:2]
    top_slots = [
        captcha_img[int(h * 0.22):int(h * 0.48), int(w * 0.05):int(w * 0.33)],
        captcha_img[int(h * 0.22):int(h * 0.48), int(w * 0.36):int(w * 0.64)],
        captcha_img[int(h * 0.22):int(h * 0.48), int(w * 0.67):int(w * 0.95)],
    ]
    bottom_slots = [
        captcha_img[int(h * 0.52):int(h * 0.78), int(w * 0.05):int(w * 0.33)],
        captcha_img[int(h * 0.52):int(h * 0.78), int(w * 0.36):int(w * 0.64)],
        captcha_img[int(h * 0.52):int(h * 0.78), int(w * 0.67):int(w * 0.95)],
    ]
    if any(slot.size == 0 for slot in top_slots + bottom_slots):
        raise ValueError("Não foi possível recortar os slots; ajuste as coordenadas em extract_slots().")
    return top_slots, bottom_slots


def _read_image(path):
    """Lê também caminhos com acentos no Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def resolve_captcha(captcha_path):
    img = _read_image(captcha_path)
    if img is None:
        raise FileNotFoundError(f"Não consegui abrir a imagem: {captcha_path}")

    top_crops, bottom_crops = extract_slots(img)
    top_embeds = np.stack([get_image_embedding(crop) for crop in top_crops])
    bottom_embeds = np.stack([get_image_embedding(crop) for crop in bottom_crops])

    # Como os embeddings estão normalizados, o produto escalar é similaridade cosseno.
    similarities = top_embeds @ bottom_embeds.T
    rows, cols = linear_sum_assignment(1.0 - similarities)
    pairs = [(int(top_idx), int(bottom_idx)) for top_idx, bottom_idx in zip(rows, cols)]

    for top_idx, bottom_idx in pairs:
        score = similarities[top_idx, bottom_idx]
        print(f"Slot superior {top_idx + 1} corresponde ao inferior {bottom_idx + 1} "
              f"(similaridade: {score:.3f})")
    return pairs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pareia os ícones de uma captura de captcha.")
    parser.add_argument("imagem", nargs="?", default="captcha.png", help="caminho da captura (padrão: captcha.png)")
    args = parser.parse_args()
    resolve_captcha(Path(args.imagem))
