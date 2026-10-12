import pytest

from spurgeon.core.hardware import Gpu, PerfilHardware, _fabricante, recomendar, threads_para_uso


def perfil(*, threads=12, ram=16.0, gpu_gb=None, cuda=False) -> PerfilHardware:
    gpus = (Gpu("GeForce Teste", "nvidia", int(gpu_gb * 1024)),) if gpu_gb else ()
    return PerfilHardware(
        cpu_nome="CPU de teste",
        nucleos_fisicos=max(1, threads // 2),
        threads_logicos=threads,
        ram_total_gb=ram,
        ram_livre_gb=ram / 2,
        disco_livre_gb=100.0,
        gpus=gpus,
        cuda_utilizavel=cuda,
        motivo_cuda="teste",
    )


@pytest.mark.parametrize(
    ("threads", "percentual", "esperado"),
    [(12, 75, 9), (12, 100, 12), (2, 75, 2), (3, 75, 2), (1, 75, 1)],
)
def test_threads_para_o_uso_escolhido(threads, percentual, esperado):
    assert threads_para_uso(perfil(threads=threads), percentual) == esperado


def test_gpu_grande_usa_large_v3_em_float16():
    r = recomendar(perfil(gpu_gb=12, cuda=True))
    assert (r.whisper_modelo, r.whisper_dispositivo, r.whisper_compute_type) == (
        "large-v3", "cuda", "float16",
    )  # fmt: skip


def test_gpu_de_6_gb_usa_int8_float16():
    r = recomendar(perfil(gpu_gb=6, cuda=True))
    assert (r.whisper_modelo, r.whisper_compute_type) == ("large-v3", "int8_float16")


def test_gpu_pequena_usa_o_turbo_na_gpu():
    r = recomendar(perfil(gpu_gb=4, cuda=True))
    assert (r.whisper_modelo, r.whisper_dispositivo) == ("large-v3-turbo", "cuda")


def test_gpu_sem_cuda_cai_para_a_cpu():
    r = recomendar(perfil(gpu_gb=2, cuda=False))
    assert (r.whisper_modelo, r.whisper_dispositivo, r.whisper_compute_type) == (
        "large-v3-turbo", "cpu", "int8",
    )  # fmt: skip
    assert any("Sem GPU utilizável" in a for a in r.avisos)


def test_pc_com_poucos_threads_recebe_modelo_menor():
    r = recomendar(perfil(threads=2))
    assert r.whisper_modelo == "small"
    assert r.llm_modelo == "qwen3:4b"


@pytest.mark.parametrize(
    ("ram", "esperado"),
    [
        (15.8, "qwen3:8b"),
        (16.0, "qwen3:8b"),
        (12.0, "qwen3:4b"),
        (8.0, "qwen3:4b"),
        (4.0, "qwen3:1.7b"),
    ],
)
def test_llm_conforme_a_ram_na_cpu(ram, esperado):
    assert recomendar(perfil(ram=ram)).llm_modelo == esperado


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("NVIDIA GeForce GTX 760", "nvidia"),
        ("AMD Radeon RX 580", "amd"),
        ("Intel(R) UHD Graphics 630", "intel"),
        ("Adaptador qualquer", "outro"),
    ],
)
def test_fabricante_pelo_nome(nome, esperado):
    assert _fabricante(nome) == esperado
