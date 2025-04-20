"""
Standalone ugly script to read the configuration of a galil controller.
"""

### single axis A for now...

from bliss.comm.util import get_comm, TCP

conf = {"tcp": {"url": "pzmotid211.esrf.fr"}}
opt = {"port": 23}
kom = get_comm(conf, ctype=TCP, **opt)
kom.write(b"IA?\r\n")
ans = kom.raw_read().decode().strip("\r\n :")
print(ans)

print("---------config-------------")
cmd_list = ["IA?", "KP?", "KI?", "KD?", "TM?", "", "GR?"]
cmd_list.append("CE?")
cmd_list.append("IL?")  # Integrator Limit
cmd_list.append("IT?")  # Independent Time constant (smoothing)
cmd_list.append("AC?")  #
cmd_list.append("DC?")  #
cmd_list.append("SP?")  #
cmd_list.append("VS?")  # vector smoothing
cmd_list.append("VA?")  # vector acceleration
cmd_list.append("VD?")  # vector deceleration
# cmd_list.append("VT?")  # vector time (not for: DMC4010 DMC30010)
cmd_list.append("OE?")  #
cmd_list.append("ER?")  #
cmd_list.append("OF?")  #
cmd_list.append("TL?")  #
cmd_list.append("TH?")  #

for cmd in cmd_list:
    kom.write(f"{cmd}\r\n".encode())
    ans = kom.raw_read().decode().strip("\r\n :")
    print(f"{cmd}  :  {ans}")

print("---------status-------------")

cmd_list = ["TI?"]
cmd_list.append("QZ?")
cmd_list.append("TB?")
cmd_list.append("TC?")
cmd_list.append("MT?")
cmd_list.append("BR?")

cmd_list.append("TD A")
cmd_list.append("TE A")
cmd_list.append("TP A")
cmd_list.append("TT A")
cmd_list.append("TS A")

for cmd in cmd_list:
    kom.write(f"{cmd}\r\n".encode())
    ans = kom.raw_read().decode().strip("\r\n :")
    print(f"{cmd}  :  {ans}")
