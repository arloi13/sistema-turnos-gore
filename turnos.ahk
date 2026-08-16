; Si presionas el signo MÁS del teclado numérico, simula un clic o envía la señal
NumpadAdd::
    ; Esto le dice a Windows que mantenga activa o traiga al frente la ventana del navegador con el panel
    WinActivate, Panel de Operador
    Send, +
return

; Si presionas el signo MENOS del teclado numérico, repite
NumpadSub::
    WinActivate, Panel de Operador
    Send, -
return
