"""Define fixed CAB arithmetic, native bond order, procedures and verified generated assets."""

from decimal import ROUND_HALF_EVEN
from typing import Final

CAB_UNITS: Final[str] = "eV/(1E15 atoms/cm2)"
CAB_PRECISION: Final[int] = 28
CAB_ROUNDING: Final[str] = ROUND_HALF_EVEN
BRAGG_PROCEDURE_VERSION: Final[int] = 1
RECOVERY_PROCEDURE_VERSION: Final[int] = 1
CAPTURE_PROCEDURE_VERSION: Final[int] = 1
SR_BONDS: Final[tuple[tuple[int, int, str], ...]] = (
    (1, 1, "-"), (1, 6, "-"), (1, 7, "-"), (1, 8, "-"),
    (6, 6, "-"), (6, 6, "="), (6, 6, "#"), (6, 7, "-"),
    (6, 8, "-"), (6, 8, "="), (6, 9, "-"), (6, 17, "-"),
    (7, 7, "#"), (7, 8, "-"), (8, 8, "="), (1, 16, "-"),
    (6, 16, "-"), (9, 16, "-"),
)
CAPTURE_MANIFEST: Final[bytes] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><assemblyIdentity version="1.0.0.0" processorArchitecture="x86" name="SRIM.Compound.Check" type="win32"/><dependency><dependentAssembly><assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="x86" publicKeyToken="6595b64144ccf1df" language="*"/></dependentAssembly></dependency></assembly>'
MOCK_HEADER: Final[bytes] = b'# TABLE OF COMPOUNDS AND DATA. Table is organized as shown below.\r\n#    The left hand character in each line indicates its purpose :\r\n#        ! = Header for various Tables     (e.g. Nuclear Materials, Gases...)\r\n#        * = Target Description for Table  (e.g. Air, Bone, Water...)\r\n#  Next Line = Numerical Data-Line for target. Data Line is :\r\n#        (Name),(Density g/cm3),(Number of elements),(Atomic No.),(Atom %),...\r\n#    !!!! Maximum Number of elements in a target = 8,\r\n#  NOTE: Can use composition by Mass Percent (Indicate by preceding Name by "%")\r\n#    For example, for BONE in both Atomic percent and Mass percent:\r\n#        Atomic % = "Bone" ,1.92,6, 1,39.3, 6,15.1, 7,3.5, 8,31.7, 40,6.5, 15,3.9\r\n#        Mass   % = "%Bone",1.92,6, 1,3.37, 6,1.29, 7,0.3, 8,2.72, 40,0.56,15,0.33\r\n#  Next Line = Numerical Bond-Structure data for target. Bonding Line is :\r\n#              (H-H)(H-C)(H-N)(H-O) (C-C)(C=C)(C\xf0C)(C-N)(C-O)(C=O)(C-F)(C-Cl)\r\n#              (N\xf0N)(N-O)(O=O) (S-H)(S-C)(S-F)\r\n#        $ = Comments on target (maximum of 8 lines / target)\r\n#  ***   After the above is input, the next compound starts.\r\n#  ***   Look at some of the examples below, and this will be clearer.\r\n#-------------------------------------------------------------------------------\r\n#  New targets (H.P. 2010): 518 NbPentoxide, 560 PEN, 712 ZrDioxide\r\n#  Blood, human (H.P., 22 Feb 2011)\r\n#  SiC (No. 590) density changed to theoretical 3.21 g/cm3 (H.P., 15 Feb 2011)\r\n#  Silicon Nitride (ICRU 591) added (HP, 7 Mar 2011)\r\n#  PHB (ICRU-562) added (31 Mar 11), KCl (6 Aug 11), InN (29 Sep 11)\r\n#  Fluoroform, vinyl fluoride, vinyl chloride added (HP, 11 Oct 11)\r\n#  Isobutane (HP, 12 Dec 12)\r\n#-------------------------------------------------------------------------------\r\n!\r\n!\xdf\xdf\xdf\xdf\xdf\xdf   NUCLEAR  PHYSICS  MATERIALS  \xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\xdf\r\n!                                Density       Atomic Stoichiometry\r\n!     Common  Name               (g/cm3)       (Atoms/Molecule or Atom Percent)\r\n!\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd  \xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd   \xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\xcd\r\n'
