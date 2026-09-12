// Avisa o servidor sempre que a página está prestes a ser fechada
// (fechar a aba, fechar o navegador, dar F5 ou navegar para outra
// página). O servidor decide se isso foi um fechamento de verdade ou
// só um F5/navegação normal - ver /logout-beacon e
// verificar_fechamento_de_aba no app.py.
window.addEventListener("pagehide", function () {
  navigator.sendBeacon("/logout-beacon");
});
