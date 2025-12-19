import inspect
from PyLTSpice import SimRunner, SpiceEditor

print("SimRunner init args:")
print(inspect.signature(SimRunner.__init__))

print("SimRunner run args:")
print(inspect.signature(SimRunner.run))

print("\nSpiceEditor methods:")
print([m for m in dir(SpiceEditor) if 'remove' in m])
