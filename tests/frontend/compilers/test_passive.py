# Copyright 2019 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
r"""Unit tests for the GaussianUnitary class"""

import pytest
import numpy as np

import strawberryfields as sf
import strawberryfields.ops as ops
from strawberryfields.utils import random_symplectic

from scipy.stats import unitary_group

from thewalrus.symplectic import squeezing, passive_transformation

pytestmark = pytest.mark.frontend

np.random.seed(42)


@pytest.mark.parametrize("depth", [1, 3, 6])
@pytest.mark.parametrize("width", [5, 10, 15])
def test_passive_program(tol, depth, width):
    """Tests that a circuit and its compiled version produce the same Gaussian state"""
    circuit = sf.Program(width)

    T_circuit = np.eye(width, dtype=np.complex128)
    with circuit.context as q:
        for _ in range(depth):
            U = unitary_group.rvs(width)
            T_circuit = U @ T_circuit
            ops.Interferometer(U) | q
            for i in range(width):
                ops.LossChannel(0.5) | q[i]
            T_circuit *= np.sqrt(0.5)

    compiled_circuit = circuit.compile(compiler='passive')
    T = compiled_circuit.circuit[0].op.p[0]
    assert np.allclose(T, T_circuit, atol=tol, rtol=0)

def test_all_passive_gates(tol):
    """test that all gates run and do not cause anything to crash"""

    eng = sf.LocalEngine(backend="gaussian")
    circuit = sf.Program(4)

    with circuit.context as q:
        for i in range(4):
            ops.Sgate(1, 0.3) | q[i]
        ops.Rgate(np.pi) | q[0]
        ops.LossChannel(0.9) | q[1]
        ops.MZgate(0.25 * np.pi, 0) | (q[2], q[3])
        ops.BSgate(0.8, 0.4) | (q[1], q[3])
        ops.Interferometer(0.5 ** 0.5 * np.fft.fft(np.eye(2))) | (q[0], q[2])
        ops.PassiveChannel(0.1 * np.ones((3,3))) | (q[3], q[1], q[0])

    cov = eng.run(circuit).state.cov()

    circuit = sf.Program(4)
    with circuit.context as q:
        ops.Rgate(np.pi) | q[0]
        ops.LossChannel(0.9) | q[1]
        ops.MZgate(0.25 * np.pi, 0) | (q[2], q[3])
        ops.BSgate(0.8, 0.4) | (q[1], q[3])
        ops.Interferometer(0.5 ** 0.5 * np.fft.fft(np.eye(2))) | (q[0], q[2])
        ops.PassiveChannel(0.1 * np.ones((3,3))) | (q[3], q[1], q[0])
   
    compiled_circuit = circuit.compile(compiler='passive')
    T = compiled_circuit.circuit[0].op.p[0]
    
    S_sq = squeezing([1] * 4, [0.3] * 4)
    cov_sq = S_sq @ S_sq.T 
    mu = np.zeros(8)

    _, cov2 = passive_transformation(mu, cov_sq, T)

    assert np.allclose(cov, cov2, atol=tol, rtol=0)

@pytest.mark.parametrize("depth", [1, 2, 3])
def test_modes_subset(depth):
    """Tests that the compiler recognizes which modes are not being modified and acts accordingly"""

    width = 10
    eng = sf.LocalEngine(backend="gaussian")
    eng1 = sf.LocalEngine(backend="gaussian")
    circuit = sf.Program(width)
    indices = (1, 4, 2, 6, 7)
    active_modes = len(indices)
    with circuit.context as q:
        for _ in range(depth):
            U = unitary_group.rvs(len(indices))
            ops.Interferometer(U) | tuple(q[i] for i in indices)

    compiled_circuit = circuit.compile(compiler="passive")

    assert len(compiled_circuit.circuit[0].reg) == 5
    indices = [compiled_circuit.circuit[0].reg[i].ind for i in range(5)]
    assert indices == sorted(list(indices))

