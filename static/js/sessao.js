// Desloga automaticamente ao fechar a aba/navegador - mas NÃO ao navegar
// para outra página do próprio site, nem ao dar F5.
//
// Como funciona: antes de qualquer navegação normal (clique em link,
// envio de formulário ou F5), marcamos uma "bandeira" local no
// navegador. Quando a página está de fato sendo descartada (evento
// "pagehide"), conferimos essa bandeira:
// - se ela estiver marcada, foi só uma navegação normal - não desloga.
// - se não estiver marcada, a aba/navegador está fechando de verdade -
//   avisamos o servidor para encerrar a sessão.
//
// Tudo isso é decidido aqui no navegador, sem depender de tempo de rede,
// para evitar problemas de "corrida" entre pedidos.
(function () {
  var CHAVE_NAVEGANDO = "navegandoNoSite";

  function marcarNavegacaoInterna() {
    sessionStorage.setItem(CHAVE_NAVEGANDO, "1");
  }

  // Clique em qualquer link do próprio site.
  document.addEventListener("click", function (evento) {
    var link = evento.target.closest("a[href]");
    if (link && link.origin === window.location.origin) {
      marcarNavegacaoInterna();
    }
  });

  // Envio de formulário (salvar cadastro, login, etc).
  document.addEventListener("submit", marcarNavegacaoInterna);

  // Campos <select> que mudam de página sozinhos ao trocar de valor
  // (ex: filtro "onchange='this.form.submit()'"). Chamar form.submit()
  // via JavaScript NÃO dispara o evento "submit" - só o "change" - por
  // isso essa escuta separada é necessária.
  document.addEventListener("change", marcarNavegacaoInterna);

  // F5, Ctrl+R ou Cmd+R (atualizar a página).
  document.addEventListener("keydown", function (evento) {
    var ehF5 = evento.key === "F5";
    var ehCtrlR = (evento.ctrlKey || evento.metaKey) && (evento.key === "r" || evento.key === "R");
    if (ehF5 || ehCtrlR) {
      marcarNavegacaoInterna();
    }
  });

  window.addEventListener("pagehide", function () {
    if (sessionStorage.getItem(CHAVE_NAVEGANDO) === "1") {
      sessionStorage.removeItem(CHAVE_NAVEGANDO);
      return;
    }
    navigator.sendBeacon("/logout-beacon");
  });
})();
