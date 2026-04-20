import subprocess
from pathlib import Path

def create_task(name, command, args, schedule, start_time=None, extra=""):
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <Triggers>\n'
        + (
            f'    <CalendarTrigger>\n'
            f'      <StartBoundary>2026-01-01T{start_time}:00</StartBoundary>\n'
            f'      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n'
            f'    </CalendarTrigger>\n'
            if schedule == "daily" else
            f'    <TimeTrigger>\n'
            f'      <StartBoundary>2026-01-01T{start_time}:00</StartBoundary>\n'
            f'      <Repetition>\n'
            f'        <Interval>PT3H</Interval>\n'
            f'        <StopAtDurationEnd>false</StopAtDurationEnd>\n'
            f'      </Repetition>\n'
            f'    </TimeTrigger>\n'
            if schedule == "every3h" else
            '    <BootTrigger><Enabled>true</Enabled></BootTrigger>\n'
        )
        + '  </Triggers>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Actions>\n'
        '    <Exec>\n'
        f'      <Command>{command}</Command>\n'
        f'      <Arguments>{args}</Arguments>\n'
        '      <WorkingDirectory>C:\\Users\\walli\\octodamus</WorkingDirectory>\n'
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
    )
    xml_path = str(Path(r'C:\Users\walli\octodamus') / f'_task_{name}.xml')
    with open(xml_path, 'w', encoding='utf-16') as f:
        f.write(xml)
    result = subprocess.run(
        ['powershell', '-Command', f'schtasks /create /tn "{name}" /xml "{xml_path}" /f'],
        capture_output=True, text=True
    )
    print(f'{name}: {result.stdout.strip() or result.stderr.strip()}')
    Path(xml_path).unlink(missing_ok=True)

# 1. BOTCOIN miner -- logs to logs/botcoin_miner.log via cmd redirect
_LOG  = r'C:\Users\walli\octodamus\logs\botcoin_miner.log'
_PY   = r'C:\Python314\python.exe'
_SCR  = r'C:\Users\walli\octodamus\octo_boto_botcoin.py'
_ARGS = f'/c "{_PY} {_SCR} --loop >> {_LOG} 2>&amp;1"'
_WD   = r'C:\Users\walli\octodamus'

miner_xml = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <Triggers>\n'
    '    <BootTrigger><Enabled>true</Enabled></BootTrigger>\n'
    '  </Triggers>\n'
    '  <Settings>\n'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
    '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
    '    <Enabled>true</Enabled>\n'
    '  </Settings>\n'
    '  <Actions>\n'
    '    <Exec>\n'
    '      <Command>cmd.exe</Command>\n'
    f'      <Arguments>{_ARGS}</Arguments>\n'
    f'      <WorkingDirectory>{_WD}</WorkingDirectory>\n'
    '    </Exec>\n'
    '  </Actions>\n'
    '</Task>\n'
)
xml_path = r'C:\Users\walli\octodamus\_task_miner.xml'
with open(xml_path, 'w', encoding='utf-16') as f:
    f.write(miner_xml)
r = subprocess.run(['powershell','-Command',f'schtasks /create /tn "Octodamus-BOTCOIN-Miner" /xml "{xml_path}" /f'],
    capture_output=True, text=True)
print(f'Miner task: {r.stdout.strip() or r.stderr.strip()}')
Path(xml_path).unlink(missing_ok=True)

# 2. Monitor -- every 3 hours starting 00:00
monitor_xml = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <Triggers>\n'
    '    <TimeTrigger>\n'
    '      <StartBoundary>2026-01-01T00:00:00</StartBoundary>\n'
    '      <Repetition>\n'
    '        <Interval>PT3H</Interval>\n'
    '        <StopAtDurationEnd>false</StopAtDurationEnd>\n'
    '      </Repetition>\n'
    '    </TimeTrigger>\n'
    '  </Triggers>\n'
    '  <Settings>\n'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
    '    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>\n'
    '    <Enabled>true</Enabled>\n'
    '  </Settings>\n'
    '  <Actions>\n'
    '    <Exec>\n'
    '      <Command>C:\\Python314\\python.exe</Command>\n'
    '      <Arguments>C:\\Users\\walli\\octodamus\\octo_botcoin_monitor.py</Arguments>\n'
    '      <WorkingDirectory>C:\\Users\\walli\\octodamus</WorkingDirectory>\n'
    '    </Exec>\n'
    '  </Actions>\n'
    '</Task>\n'
)
xml_path = r'C:\Users\walli\octodamus\_task_monitor.xml'
with open(xml_path, 'w', encoding='utf-16') as f:
    f.write(monitor_xml)
r = subprocess.run(['powershell','-Command',f'schtasks /create /tn "Octodamus-BOTCOIN-Monitor" /xml "{xml_path}" /f'],
    capture_output=True, text=True)
print(f'Monitor task: {r.stdout.strip() or r.stderr.strip()}')
Path(xml_path).unlink(missing_ok=True)

# 3. Report 6am
report_6am_xml = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <Triggers>\n'
    '    <CalendarTrigger>\n'
    '      <StartBoundary>2026-01-01T06:00:00</StartBoundary>\n'
    '      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n'
    '    </CalendarTrigger>\n'
    '  </Triggers>\n'
    '  <Settings>\n'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
    '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
    '    <Enabled>true</Enabled>\n'
    '  </Settings>\n'
    '  <Actions>\n'
    '    <Exec>\n'
    '      <Command>C:\\Python314\\python.exe</Command>\n'
    '      <Arguments>C:\\Users\\walli\\octodamus\\octo_botcoin_report.py morning</Arguments>\n'
    '      <WorkingDirectory>C:\\Users\\walli\\octodamus</WorkingDirectory>\n'
    '    </Exec>\n'
    '  </Actions>\n'
    '</Task>\n'
)
xml_path = r'C:\Users\walli\octodamus\_task_report6am.xml'
with open(xml_path, 'w', encoding='utf-16') as f:
    f.write(report_6am_xml)
r = subprocess.run(['powershell','-Command',f'schtasks /create /tn "Octodamus-BOTCOIN-Report-6am" /xml "{xml_path}" /f'],
    capture_output=True, text=True)
print(f'Report 6am task: {r.stdout.strip() or r.stderr.strip()}')
Path(xml_path).unlink(missing_ok=True)

# 4. Report 6pm
report_6pm_xml = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <Triggers>\n'
    '    <CalendarTrigger>\n'
    '      <StartBoundary>2026-01-01T18:00:00</StartBoundary>\n'
    '      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n'
    '    </CalendarTrigger>\n'
    '  </Triggers>\n'
    '  <Settings>\n'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
    '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
    '    <Enabled>true</Enabled>\n'
    '  </Settings>\n'
    '  <Actions>\n'
    '    <Exec>\n'
    '      <Command>C:\\Python314\\python.exe</Command>\n'
    '      <Arguments>C:\\Users\\walli\\octodamus\\octo_botcoin_report.py evening</Arguments>\n'
    '      <WorkingDirectory>C:\\Users\\walli\\octodamus</WorkingDirectory>\n'
    '    </Exec>\n'
    '  </Actions>\n'
    '</Task>\n'
)
xml_path = r'C:\Users\walli\octodamus\_task_report6pm.xml'
with open(xml_path, 'w', encoding='utf-16') as f:
    f.write(report_6pm_xml)
r = subprocess.run(['powershell','-Command',f'schtasks /create /tn "Octodamus-BOTCOIN-Report-6pm" /xml "{xml_path}" /f'],
    capture_output=True, text=True)
print(f'Report 6pm task: {r.stdout.strip() or r.stderr.strip()}')
Path(xml_path).unlink(missing_ok=True)
