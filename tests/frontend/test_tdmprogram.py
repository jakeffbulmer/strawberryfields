# Copyright 2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
r"""Unit tests for tdmprogram.py"""
import copy
from collections.abc import Iterable

import inspect
from strawberryfields.program_utils import CircuitError
import pytest
import numpy as np

import blackbird as bb
import strawberryfields as sf
from strawberryfields import ops
from strawberryfields.tdm import tdmprogram
from strawberryfields.tdm.tdmprogram import move_vac_modes, reshape_samples
from strawberryfields.api.devicespec import DeviceSpec

pytestmark = pytest.mark.frontend

# make test deterministic
np.random.seed(42)


def singleloop(r, alpha, phi, theta, shots, shift="default"):
    """Single-loop program.

    Args:
        r (float): squeezing parameter
        alpha (Sequence[float]): beamsplitter angles
        phi (Sequence[float]): rotation angles
        theta (Sequence[float]): homodyne measurement angles
        shots (int): number of shots
        shift (string): type of shift used in the program
    Returns:
        (list): homodyne samples from the single loop simulation
    """
    prog = tdmprogram.TDMProgram(N=2)
    with prog.context(alpha, phi, theta, shift=shift) as (p, q):
        ops.Sgate(r, 0) | q[1]
        ops.BSgate(p[0]) | (q[0], q[1])
        ops.Rgate(p[1]) | q[1]
        ops.MeasureHomodyne(p[2]) | q[0]
    eng = sf.Engine("gaussian")
    result = eng.run(prog, shots=shots)

    return result.samples


class TestTDMErrorRaising:
    """Test that the correct error messages are raised when a TDMProgram is created"""

    def test_gates_equal_length(self):
        """Checks gate list parameters have same length"""
        sq_r = 1.0
        c = 4
        shots = 10
        alpha = [0, np.pi / 4] * c
        phi = [np.pi / 2, 0] * c
        theta = [0, 0] + [0, np.pi / 2] + [np.pi / 2, 0] + [np.pi / 2]
        with pytest.raises(ValueError, match="Gate-parameter lists must be of equal length."):
            singleloop(sq_r, alpha, phi, theta, shots)

    def test_at_least_one_measurement(self):
        """Checks circuit has at least one measurement operator"""
        sq_r = 1.0
        N = 3
        shots = 1
        alpha = [0] * 4
        phi = [0] * 4
        prog = tdmprogram.TDMProgram(N=N)
        with pytest.raises(ValueError, match="Must be at least one measurement."):
            with prog.context(alpha, phi, shift="default") as (p, q):
                ops.Sgate(sq_r, 0) | q[2]
                ops.BSgate(p[0]) | (q[1], q[2])
                ops.Rgate(p[1]) | q[2]
            eng = sf.Engine("gaussian")
            eng.run(prog, shots=shots)

    def test_spatial_modes_number_of_measurements_match(self):
        """Checks number of spatial modes matches number of measurements"""
        sq_r = 1.0
        shots = 1
        alpha = [0] * 4
        phi = [0] * 4
        theta = [0] * 4
        with pytest.raises(
            ValueError, match="Number of measurement operators must match number of spatial modes."
        ):
            prog = tdmprogram.TDMProgram(N=[3, 3])
            with prog.context(alpha, phi, theta) as (p, q):
                ops.Sgate(sq_r, 0) | q[2]
                ops.BSgate(p[0]) | (q[1], q[2])
                ops.Rgate(p[1]) | q[2]
                ops.MeasureHomodyne(p[2]) | q[0]
            eng = sf.Engine("gaussian")
            result = eng.run(prog, shots=shots)

    def test_passing_list_of_tdmprograms(self):
        """Test that error is raised when passing a list containing TDM programs"""
        prog = tdmprogram.TDMProgram(N=2)
        with prog.context([1, 1], [1, 1], [1, 1]) as (p, q):
            ops.Sgate(0, 0) | q[1]
            ops.BSgate(p[0]) | (q[0], q[1])
            ops.Rgate(p[1]) | q[1]
            ops.MeasureHomodyne(p[2]) | q[0]

        eng = sf.Engine("gaussian")

        with pytest.raises(
            NotImplementedError, match="Lists of TDM programs are not currently supported"
        ):
            eng.run([prog, prog])


def test_shift_by_specified_amount():
    """Checks that shifting by 1 is equivalent to shift='end' for a program
    with one spatial mode"""
    np.random.seed(42)
    sq_r = 1.0
    shots = 1
    alpha = [0] * 4
    phi = [0] * 4
    theta = [0] * 4
    np.random.seed(42)
    x = singleloop(sq_r, alpha, phi, theta, shots)
    np.random.seed(42)
    y = singleloop(sq_r, alpha, phi, theta, shots, shift=1)
    assert np.allclose(x, y)


def test_str_tdm_method():
    """Testing the string method"""
    prog = tdmprogram.TDMProgram(N=1)
    assert prog.__str__() == "<TDMProgram: concurrent modes=1, time bins=0, spatial modes=0>"


def test_single_parameter_list_program():
    """Test that a TDMProgram with a single parameter list works."""
    prog = sf.TDMProgram(2)
    eng = sf.Engine("gaussian")

    with prog.context([1, 2]) as (p, q):
        ops.Sgate(p[0]) | q[0]
        ops.MeasureHomodyne(p[0]) | q[0]

    eng.run(prog)

    assert isinstance(prog.loop_vars, Iterable)
    assert prog.parameters == {'p0': [1, 2]}


class TestSingleLoopNullifier:
    """Groups tests where a nullifier associated with a state generated by a oneloop setup is checked."""

    def test_epr(self):
        """Generates an EPR state and checks that the correct correlations (noise reductions) are observed
        from the samples"""
        np.random.seed(42)
        vac_modes = 1
        sq_r = 5.0
        c = 2
        shots = 100

        # This will generate c EPRstates per copy. I chose c = 4 because it allows us to make 4 EPR pairs per copy that can each be measured in different basis permutations.
        alpha = [0, np.pi / 4] * c
        phi = [np.pi / 2, 0] * c

        # Measurement of 2 subsequent EPR states in XX, PP to investigate nearest-neighbour correlations in all basis permutations
        theta = [np.pi / 2, 0, 0, np.pi / 2]

        timebins_per_shot = len(alpha)

        samples = singleloop(sq_r, alpha, phi, theta, shots)
        reshaped_samples = move_vac_modes(samples, 2, crop=True)

        X0 = reshaped_samples[:, 0, 0]
        X1 = reshaped_samples[:, 0, 1]
        P2 = reshaped_samples[:, 0, 2]
        P3 = reshaped_samples[:, 0, 3]

        rtol = 5 / np.sqrt(shots)
        minusstdX1X0 = (X1 - X0).var()
        plusstdX1X0 = (X1 + X0).var()
        squeezed_std = np.exp(-2 * sq_r)
        expected_minus = sf.hbar * squeezed_std
        expected_plus = sf.hbar / squeezed_std
        assert np.allclose(minusstdX1X0, expected_minus, rtol=rtol)
        assert np.allclose(plusstdX1X0, expected_plus, rtol=rtol)

        minusstdP2P3 = (P2 - P3).var()
        plusstdP2P3 = (P2 + P3).var()
        assert np.allclose(minusstdP2P3, expected_plus, rtol=rtol)
        assert np.allclose(plusstdP2P3, expected_minus, rtol=rtol)

    def test_ghz(self):
        """Generates a GHZ state and checks that the correct correlations (noise reductions) are observed
        from the samples
        See Eq. 5 of https://advances.sciencemag.org/content/5/5/eaaw4530
        """
        # Set up the circuit
        np.random.seed(42)
        vac_modes = 1
        n = 4
        shots = 100
        sq_r = 5
        alpha = [np.arccos(np.sqrt(1 / (n - i + 1))) if i != n + 1 else 0 for i in range(n)]
        alpha[0] = 0.0
        phi = [0] * n
        phi[0] = np.pi / 2
        timebins_per_shot = len(alpha)

        # Measuring X nullifier
        theta = [0] * n
        samples_X = singleloop(sq_r, alpha, phi, theta, shots)
        reshaped_samples_X = move_vac_modes(samples_X, 2, crop=True)

        # Measuring P nullifier
        theta = [np.pi / 2] * n
        samples_P = singleloop(sq_r, alpha, phi, theta, shots)
        reshaped_samples_P = move_vac_modes(samples_P, 2, crop=True)

        # We will check that the x of all the modes equal the x of the last one
        nullifier_X = lambda sample: (sample - sample[-1])[:-1]
        val_nullifier_X = np.var([nullifier_X(x[0]) for x in reshaped_samples_X], axis=0)
        assert np.allclose(val_nullifier_X, sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(shots))

        # We will check that the sum of all the p is equal to zero
        val_nullifier_P = np.var([np.sum(p[0]) for p in reshaped_samples_P], axis=0)
        assert np.allclose(
            val_nullifier_P, 0.5 * sf.hbar * n * np.exp(-2 * sq_r), rtol=5 / np.sqrt(shots)
        )

    def test_one_dimensional_cluster(self):
        """Test that the nullifier have the correct value in the experiment described in
        See Eq. 10 of https://advances.sciencemag.org/content/5/5/eaaw4530
        """
        np.random.seed(42)
        vac_modes = 1
        n = 20
        shots = 100
        sq_r = 3
        alpha_c = np.arccos(np.sqrt((np.sqrt(5) - 1) / 2))
        alpha = [alpha_c] * n
        alpha[0] = 0.0
        phi = [np.pi / 2] * n
        theta = [0, np.pi / 2] * (
            n // 2
        )  # Note that we measure x for mode i and the p for mode i+1.
        timebins_per_shot = len(alpha)

        reshaped_samples = singleloop(sq_r, alpha, phi, theta, shots)

        nullifier = lambda x: np.array(
            [-x[i - 2] + x[i - 1] - x[i] for i in range(2, len(x) - 2, 2)]
        )[1:]
        nullifier_samples = np.array([nullifier(y[0]) for y in reshaped_samples])
        delta = np.var(nullifier_samples, axis=0)
        assert np.allclose(delta, 1.5 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(shots))


def test_one_dimensional_cluster_tokyo():
    """
    One-dimensional temporal-mode cluster state as demonstrated in
    https://aip.scitation.org/doi/pdf/10.1063/1.4962732
    """
    np.random.seed(42)
    sq_r = 5

    n = 10  # for an n-mode cluster state
    shots = 3

    # first half of cluster state measured in X, second half in P
    theta1 = [0] * int(n / 2) + [np.pi / 2] * int(n / 2)  # measurement angles for detector A
    theta2 = theta1  # measurement angles for detector B

    prog = tdmprogram.TDMProgram(N=[1, 2])
    with prog.context(theta1, theta2, shift="default") as (p, q):
        ops.Sgate(sq_r, 0) | q[0]
        ops.Sgate(sq_r, 0) | q[2]
        ops.Rgate(np.pi / 2) | q[0]
        ops.BSgate(np.pi / 4) | (q[0], q[2])
        ops.BSgate(np.pi / 4) | (q[0], q[1])
        ops.MeasureHomodyne(p[0]) | q[0]
        ops.MeasureHomodyne(p[1]) | q[1]
    eng = sf.Engine("gaussian")

    result = eng.run(prog, shots=shots)
    reshaped_samples = result.samples

    for sh in range(shots):
        X_A = reshaped_samples[sh][0][: n // 2]  # X samples from detector A
        P_A = reshaped_samples[sh][0][n // 2 :]  # P samples from detector A
        X_B = reshaped_samples[sh][1][: n // 2]  # X samples from detector B
        P_B = reshaped_samples[sh][1][n // 2 :]  # P samples from detector B

        # nullifiers defined in https://aip.scitation.org/doi/pdf/10.1063/1.4962732, Eqs. (1a) and (1b)
        ntot = len(X_A) - 1
        nX = np.array([X_A[i] + X_B[i] + X_A[i + 1] - X_B[i + 1] for i in range(ntot)])
        nP = np.array([P_A[i] + P_B[i] - P_A[i + 1] + P_B[i + 1] for i in range(ntot)])

        nXvar = np.var(nX)
        nPvar = np.var(nP)

        assert np.allclose(nXvar, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(n))
        assert np.allclose(nPvar, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(n))


def test_two_dimensional_cluster_denmark():
    """
    Two-dimensional temporal-mode cluster state as demonstrated in https://arxiv.org/pdf/1906.08709
    """
    np.random.seed(42)
    sq_r = 3
    delay1 = 1  # number of timebins in the short delay line
    delay2 = 12  # number of timebins in the long delay line
    n = 200  # number of timebins
    shots = 10
    # first half of cluster state measured in X, second half in P

    theta_A = [0] * int(n / 2) + [np.pi / 2] * int(n / 2)  # measurement angles for detector A
    theta_B = theta_A  # measurement angles for detector B

    # 2D cluster
    prog = tdmprogram.TDMProgram([1, delay2 + delay1 + 1])
    with prog.context(theta_A, theta_B, shift="default") as (p, q):
        ops.Sgate(sq_r, 0) | q[0]
        ops.Sgate(sq_r, 0) | q[delay2 + delay1 + 1]
        ops.Rgate(np.pi / 2) | q[delay2 + delay1 + 1]
        ops.BSgate(np.pi / 4, np.pi) | (q[delay2 + delay1 + 1], q[0])
        ops.BSgate(np.pi / 4, np.pi) | (q[delay2 + delay1], q[0])
        ops.BSgate(np.pi / 4, np.pi) | (q[delay1], q[0])
        ops.MeasureHomodyne(p[1]) | q[0]
        ops.MeasureHomodyne(p[0]) | q[delay1]
    eng = sf.Engine("gaussian")
    result = eng.run(prog, shots=shots)
    reshaped_samples = result.samples

    for sh in range(shots):
        X_A = reshaped_samples[sh][0][: n // 2]  # X samples from detector A
        P_A = reshaped_samples[sh][0][n // 2 :]  # P samples from detector A
        X_B = reshaped_samples[sh][1][: n // 2]  # X samples from detector B
        P_B = reshaped_samples[sh][1][n // 2 :]  # P samples from detector B

        # nullifiers defined in https://arxiv.org/pdf/1906.08709.pdf, Eqs. (1) and (2)
        N = delay2
        ntot = len(X_A) - delay2 - 1
        nX = np.array(
            [
                X_A[k]
                + X_B[k]
                - X_A[k + 1]
                - X_B[k + 1]
                - X_A[k + N]
                + X_B[k + N]
                - X_A[k + N + 1]
                + X_B[k + N + 1]
                for k in range(ntot)
            ]
        )
        nP = np.array(
            [
                P_A[k]
                + P_B[k]
                + P_A[k + 1]
                + P_B[k + 1]
                - P_A[k + N]
                + P_B[k + N]
                + P_A[k + N + 1]
                - P_B[k + N + 1]
                for k in range(ntot)
            ]
        )
        nXvar = np.var(nX)
        nPvar = np.var(nP)

        assert np.allclose(nXvar, 4 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))
        assert np.allclose(nPvar, 4 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))


def test_two_dimensional_cluster_tokyo():
    """
    Two-dimensional temporal-mode cluster state as demonstrated by Universtiy of Tokyo. See: https://arxiv.org/pdf/1903.03918.pdf
    """
    # temporal delay in timebins for each spatial mode
    delayA = 0
    delayB = 1
    delayC = 5
    delayD = 0

    # concurrent modes in each spatial mode
    concurrA = 1 + delayA
    concurrB = 1 + delayB
    concurrC = 1 + delayC
    concurrD = 1 + delayD

    N = [concurrA, concurrB, concurrC, concurrD]

    sq_r = 5

    # first half of cluster state measured in X, second half in P
    n = 400  # number of timebins
    theta_A = [0] * int(n / 2) + [np.pi / 2] * int(n / 2)  # measurement angles for detector A
    theta_B = theta_A  # measurement angles for detector B
    theta_C = theta_A
    theta_D = theta_A

    shots = 10

    # 2D cluster
    prog = tdmprogram.TDMProgram(N)
    with prog.context(theta_A, theta_B, theta_C, theta_D, shift="default") as (p, q):

        ops.Sgate(sq_r, 0) | q[0]
        ops.Sgate(sq_r, 0) | q[2]
        ops.Sgate(sq_r, 0) | q[8]
        ops.Sgate(sq_r, 0) | q[9]

        ops.Rgate(np.pi / 2) | q[0]
        ops.Rgate(np.pi / 2) | q[8]

        ops.BSgate(np.pi / 4) | (q[0], q[2])
        ops.BSgate(np.pi / 4) | (q[8], q[9])
        ops.BSgate(np.pi / 4) | (q[2], q[8])
        ops.BSgate(np.pi / 4) | (q[0], q[1])
        ops.BSgate(np.pi / 4) | (q[3], q[9])

        ops.MeasureHomodyne(p[0]) | q[0]
        ops.MeasureHomodyne(p[1]) | q[1]
        ops.MeasureHomodyne(p[2]) | q[3]
        ops.MeasureHomodyne(p[3]) | q[9]

    eng = sf.Engine("gaussian")
    result = eng.run(prog, shots=shots)
    reshaped_samples = result.samples

    for sh in range(shots):

        X_A = reshaped_samples[sh][0][: n // 2]  # X samples from detector A
        P_A = reshaped_samples[sh][0][n // 2 :]  # P samples from detector A
        X_B = reshaped_samples[sh][1][: n // 2]  # X samples from detector B
        P_B = reshaped_samples[sh][1][n // 2 :]  # P samples from detector B
        X_C = reshaped_samples[sh][2][: n // 2]  # X samples from detector C
        P_C = reshaped_samples[sh][2][n // 2 :]  # P samples from detector C
        X_D = reshaped_samples[sh][3][: n // 2]  # X samples from detector D
        P_D = reshaped_samples[sh][3][n // 2 :]  # P samples from detector D

        N = delayC
        # nullifiers defined in https://arxiv.org/pdf/1903.03918.pdf, Fig. S5
        ntot = len(X_A) - N - 1
        nX1 = np.array(
            [
                X_A[k]
                + X_B[k]
                - np.sqrt(1 / 2) * (-X_A[k + 1] + X_B[k + 1] + X_C[k + N] + X_D[k + N])
                for k in range(ntot)
            ]
        )
        nX2 = np.array(
            [
                X_C[k]
                - X_D[k]
                - np.sqrt(1 / 2) * (-X_A[k + 1] + X_B[k + 1] - X_C[k + N] - X_D[k + N])
                for k in range(ntot)
            ]
        )
        nP1 = np.array(
            [
                P_A[k]
                + P_B[k]
                + np.sqrt(1 / 2) * (-P_A[k + 1] + P_B[k + 1] + P_C[k + N] + P_D[k + N])
                for k in range(ntot)
            ]
        )
        nP2 = np.array(
            [
                P_C[k]
                - P_D[k]
                + np.sqrt(1 / 2) * (-P_A[k + 1] + P_B[k + 1] - P_C[k + N] - P_D[k + N])
                for k in range(ntot)
            ]
        )

        nX1var = np.var(nX1)
        nX2var = np.var(nX2)
        nP1var = np.var(nP1)
        nP2var = np.var(nP2)

        assert np.allclose(nX1var, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))
        assert np.allclose(nX2var, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))
        assert np.allclose(nP1var, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))
        assert np.allclose(nP2var, 2 * sf.hbar * np.exp(-2 * sq_r), rtol=5 / np.sqrt(ntot))


def singleloop_program(r, alpha, phi, theta):
    """Single delay loop with program.

    Args:
        r (float): squeezing parameter
        alpha (Sequence[float]): beamsplitter angles
        phi (Sequence[float]): rotation angles
        theta (Sequence[float]): homodyne measurement angles
    Returns:
        (array): homodyne samples from the single loop simulation
    """
    prog = tdmprogram.TDMProgram(N=2)
    with prog.context(alpha, phi, theta) as (p, q):
        ops.Sgate(r, 0) | q[1]
        ops.BSgate(p[0]) | (q[1], q[0])
        ops.Rgate(p[1]) | q[1]
        ops.MeasureHomodyne(p[2]) | q[0]
    return prog


target = "TD2"
tm = 4
device_spec = {
    "layout": "name template_tdm\nversion 1.0\ntarget {target} (shots=1)\ntype tdm (temporal_modes={tm})\nfloat array p1[1, {tm}] =\n    {{bs_array}}\nfloat array p2[1, {tm}] =\n    {{r_array}}\nfloat array p3[1, {tm}] =\n    {{m_array}}\n\nSgate(0.5643) | 1\nBSgate(p1) | (1, 0)\nRgate(p2) | 1\nMeasureHomodyne(p3) | 0\n",
    "modes": {"concurrent": 2, "spatial": 1, "temporal_max": 100},
    "compiler": ["TD2"],
    "gate_parameters": {
        "p1": [0, [0, 6.283185307179586]],
        "p2": [0, [0, 3.141592653589793], 3.141592653589793],
        "p3": [0, [0, 6.283185307179586]],
    },
}
device_spec["layout"] = device_spec["layout"].format(target=target, tm=tm)
device = DeviceSpec("TD2", device_spec, connection=None)


class TestTDMcompiler:
    """Test class for checking error messages from the compiler"""

    def test_tdm_wrong_layout(self):
        """Test the correct error is raised when the tdm circuit gates don't match the device spec"""
        sq_r = 0.5643
        c = 2
        alpha = [np.pi / 4, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = tdmprogram.TDMProgram(N=2)
        with prog.context(alpha, phi, theta) as (p, q):
            ops.Dgate(sq_r) | q[1]  # Here one should have an Sgate
            ops.BSgate(p[0]) | (q[1], q[0])
            ops.Rgate(p[1]) | q[1]
            ops.MeasureHomodyne(p[2]) | q[0]
        eng = sf.Engine("gaussian")
        with pytest.raises(
            CircuitError,
            match="The gates or the order of gates used in the Program",
        ):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_wrong_modes(self):
        """Test the correct error is raised when the tdm circuit registers don't match the device spec"""
        sq_r = 0.5643
        c = 2
        alpha = [np.pi / 4, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = tdmprogram.TDMProgram(N=2)
        with prog.context(alpha, phi, theta) as (p, q):
            ops.Sgate(sq_r) | q[1]
            ops.BSgate(p[0]) | (q[0], q[1])  # The order should be (q[1], q[0])
            ops.Rgate(p[1]) | q[1]
            ops.MeasureHomodyne(p[2]) | q[0]
        eng = sf.Engine("gaussian")
        with pytest.raises(
            CircuitError, match="due to incompatible mode ordering."
        ):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_wrong_parameters_explicit(self):
        """Test the correct error is raised when the tdm circuit explicit parameters are not within the allowed ranges"""
        sq_r = 2  # This squeezing is not in the allowed range of squeezing parameters
        c = 2
        alpha = [np.pi / 4, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="due to incompatible parameter."):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_wrong_parameters_explicit_in_list(self):
        """Test the correct error is raised when the tdm circuit explicit parameters are not within the allowed ranges"""
        sq_r = 0.5643
        c = 2
        alpha = [
            np.pi / 4,
            27,
        ] * c  # This beamsplitter phase is not in the allowed range of squeezing parameters
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="due to incompatible parameter."):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_wrong_parameter_second_argument(self):
        """Test the correct error is raised when the tdm circuit explicit parameters are not within the allowed ranges"""
        sq_r = 0.5643
        c = 2
        alpha = [np.pi / 4, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = tdmprogram.TDMProgram(N=2)
        with prog.context(alpha, phi, theta) as (p, q):
            ops.Sgate(sq_r, 0.4) | q[
                1
            ]  # Note that the Sgate has a second parameter that is non-zero
            ops.BSgate(p[0]) | (q[1], q[0])
            ops.Rgate(p[1]) | q[1]
            ops.MeasureHomodyne(p[2]) | q[0]
        eng = sf.Engine("gaussian")
        with pytest.raises(CircuitError, match="due to incompatible parameter."):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_wrong_parameters_symbolic(self):
        """Test the correct error is raised when the tdm circuit symbolic parameters are not within the allowed ranges"""
        sq_r = 0.5643
        c = 2
        alpha = [137, 0] * c  # Note that alpha is outside the allowed range
        phi = [0, np.pi / 2] * c
        theta = [0, 0] + [np.pi / 2, np.pi / 2]
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="due to incompatible parameter."):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_inconsistent_temporal_modes(self):
        """Test the correct error is raised when the tdm circuit has too many temporal modes"""
        sq_r = 0.5643
        c = 100  # Note that we are requesting more temporal modes (2*c = 200) than what is allowed.
        alpha = [0.5, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] * c
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="temporal modes, but the device"):
            prog.compile(device=device, compiler="TD2")

    def test_tdm_inconsistent_concurrent_modes(self):
        """Test the correct error is raised when the tdm circuit has too many concurrent modes"""
        device_spec1 = copy.deepcopy(device_spec)
        device_spec1["modes"][
            "concurrent"
        ] = 100  # Note that singleloop_program has only two concurrent modes
        device1 = DeviceSpec("x", device_spec1, connection=None)
        c = 1
        sq_r = 0.5643
        alpha = [0.5, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] * c
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="concurrent modes, but the device"):
            prog.compile(device=device1, compiler="TD2")

    def test_tdm_inconsistent_spatial_modes(self):
        """Test the correct error is raised when the tdm circuit has too many spatial modes"""
        device_spec1 = copy.deepcopy(device_spec)
        device_spec1["modes"][
            "spatial"
        ] = 100  # Note that singleloop_program has only one spatial mode
        device1 = DeviceSpec("x", device_spec1, connection=None)
        c = 1
        sq_r = 0.5643
        alpha = [0.5, 0] * c
        phi = [0, np.pi / 2] * c
        theta = [0, 0] * c
        prog = singleloop_program(sq_r, alpha, phi, theta)
        with pytest.raises(CircuitError, match="spatial modes, but the device"):
            prog.compile(device=device1, compiler="TD2")

class TestTDMProgramFunctions:
    """Test functions in the ``tdmprogram`` module"""

    @pytest.mark.parametrize("N, crop, expected", [
        (
            1,
            False,
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[9, 10], [11, 12]]],
        ),
        (
            1,
            True,
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[9, 10], [11, 12]]],
        ),
        (
            3,
            False,
            [[[3, 4], [5, 6]], [[7, 8], [9, 10]], [[11, 12], [0, 0]]]
        ),
        (
            [1, 4],
            False,
            [[[4, 5], [6, 7]], [[8, 9], [10, 11]], [[12, 0], [0, 0]]]
        ),
        (
            [4],
            True,
            [[[4, 5], [6, 7]], [[8, 9], [10, 11]]]
        ),
    ])
    def test_move_vac_modes(self, N, crop, expected):
        """Test the `move_vac_modes` function"""
        samples = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[9, 10], [11, 12]]])
        res = move_vac_modes(samples, N, crop=crop)

        assert np.all(res == expected)

class TestEngineTDMProgramInteraction:
    """Test the Engine class and its interaction with TDMProgram instances."""

    def test_shots_default(self):
        """Test that default shots (1) is used"""
        prog = sf.TDMProgram(2)
        eng = sf.Engine("gaussian")

        with prog.context([1,2], [3,4]) as (p, q):
            ops.Sgate(p[0]) | q[0]
            ops.MeasureHomodyne(p[1]) | q[0]

        results = eng.run(prog)
        assert results.samples.shape[0] == 1

    def test_shots_run_options(self):
        """Test that run_options takes precedence over default"""
        prog = sf.TDMProgram(2)
        eng = sf.Engine("gaussian")

        with prog.context([1,2], [3,4]) as (p, q):
            ops.Sgate(p[0]) | q[0]
            ops.MeasureHomodyne(p[1]) | q[0]

        prog.run_options = {"shots": 5}
        results = eng.run(prog)
        assert results.samples.shape[0] == 5

    def test_shots_passed(self):
        """Test that shots supplied via eng.run takes precedence over
        run_options and that run_options isn't changed"""
        prog = sf.TDMProgram(2)
        eng = sf.Engine("gaussian")

        with prog.context([1,2], [3,4]) as (p, q):
            ops.Sgate(p[0]) | q[0]
            ops.MeasureHomodyne(p[1]) | q[0]

        prog.run_options = {"shots": 5}
        results = eng.run(prog, shots=2)
        assert results.samples.shape[0] == 2
        assert prog.run_options["shots"] == 5


class TestTDMValidation:
    """Test the validation of TDMProgram against the device specs"""
    @pytest.fixture(scope="class")
    def device(self):
        target = "TD2"
        tm = 4
        layout = f"""
            name template_tdm
            version 1.0
            target {target} (shots=1)
            type tdm (temporal_modes=2)
            float array p0[1, {tm}] =
                {{rs_array}}
            float array p1[1, {tm}] =
                {{r_array}}
            float array p2[1, {tm}] =
                {{bs_array}}
            float array p3[1, {tm}] =
                {{m_array}}
            Sgate(p0) | 1
            Rgate(p1) | 0
            BSgate(p2, 0) | (0, 1)
            MeasureHomodyne(p3) | 0
        """
        device_spec = {
            "layout": inspect.cleandoc(layout),
            "modes": {"concurrent": 2, "spatial": 1, "temporal_max": 100},
            "compiler": [target],
            "gate_parameters": {
                "p0": [-1],
                "p1": [1],
                "p2": [2],
                "p3": [3],
            },
        }
        return DeviceSpec("TD2", device_spec, connection=None)
    
    @staticmethod
    def compile_test_program(device, args=(-1, 1, 2, 3)):
        """Compiles a test program with the given gate arguments."""
        alpha = [args[1]]
        beta = [args[2]]
        gamma = [args[3]]
        prog = tdmprogram.TDMProgram(N=2)
        with prog.context(alpha, beta, gamma) as (p, q):
            ops.Sgate(args[0]) | q[1]  # Note that the Sgate has a second parameter that is non-zero
            ops.Rgate(p[0]) | q[0]
            ops.BSgate(p[1]) | (q[0], q[1])
            ops.MeasureHomodyne(p[2]) | q[0]
        prog.compile(device=device, compiler=device.compiler)

    def test_validation_correct_args(self, device):
        """Test that no error is raised when the tdm circuit explicit parameters within the allowed ranges"""
        self.compile_test_program(device, args=(-1, 1, 2, 3))

    @pytest.mark.parametrize("incorrect_index", list(range(4)))
    def test_validation_incorrect_args(self, device, incorrect_index):
        """Test the correct error is raised when the tdm circuit explicit parameters are not within the allowed ranges"""
        args = [-1, 1, 2, 3]
        args[incorrect_index] = -999
        with pytest.raises(CircuitError, match="Parameter has value '-999' while its valid range is "):
            self.compile_test_program(device, args=args)
