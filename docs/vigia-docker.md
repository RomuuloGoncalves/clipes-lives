# Vigia no Docker (serviço `vigia`)

A config do painel (vigia térmico ativo/MAX/MIN e limites de RAM/CPU) funciona também com `docker compose run --rm clipes servico` direto, sem `./clipar` (inclusive no Windows/PowerShell). O serviço `vigia` do `docker-compose.yml` usa a mesma imagem, sobe como dependência do `clipes` (`depends_on`, sem condição de saúde: se ele falhar, o `clipes` sobe igual) e roda `python -m clipes.vigia`. A imagem precisa ser reconstruída (`docker compose build`) para conter `clipes/vigia.py`.

## O que ele faz (a cada 5 s)

1. **Temperatura** → grava `$OUT/.temp` (inteiro, escrita atômica) e `.temp.log`, mesmo formato do `./vigia-temp`. Fontes, em ordem: hwmon (`k10temp|zenpower|coretemp|cpu_thermal`), `thermal_zone`, LibreHardwareMonitor por HTTP (`CLIPES_LHM_URL`, padrão `http://host.docker.internal:8085/data.json`, timeout 3 s, cache 15 s; só sensores `Temperature` de CPU, máximo; GPU e `Distance to TjMax` são ignorados; consulta com prazo total de 3 s e corpo ≤ 1 MB). Sem fonte: grava `sem-sensor` e segue.
2. **Pausa**: lê `$OUT/.vigia` (`ativo max min`, padrão `1 85 75`); pausa o contêiner `clipes` em `>= MAX` e retoma em `<= MIN`. `ativo=0` só grava `.temp`. O estado de pausa vem do Docker a cada ciclo (sobrevive a reinício do vigia: ao subir, um `clipes` pausado com temperatura ≤ MAX, `ativo=0` ou sem sensor é despausado). Ao receber SIGTERM/SIGINT, despausa o que ele pausou.
3. **RAM/CPU ao vivo**: lê `$OUT/.recursos` (`mem cpus`; `-` = não mexer; `4g`, `512m` ou `*`) e, se diferir do limite atual do contêiner, aplica por `POST /containers/{id}/update` (`Memory`, `MemorySwap` = 2 × `Memory`, como o padrão do Docker, `NanoCpus`), sem reiniciar. Reduzir a RAM abaixo de 1,1 × o uso atual (`/stats`) é adiado (1 log, nova tentativa no ciclo seguinte); recusa do Docker também vira 1 log e nova tentativa.

O Docker `update` trata `Memory=0` e `NanoCpus=0` como "sem mudança" e `Memory=-1` dá erro (verificado neste host). Por isso `*` (sem limite) vira `MemTotal` e `nproc` do host, um limite efetivo que nunca é atingido.

## Segurança

O socket do Docker (`/var/run/docker.sock`) é montado **só** no `vigia`, nunca no `clipes`, no `bot` nem no `test`. Quem o acessa controla o Docker do host, então o serviço é enxuto: sem segredos nem `.env`, sem portas, `read_only`, `cap_drop: [ALL]` (só `DAC_OVERRIDE` e `CHOWN` de volta, para gravar em `/out` e devolver o dono `HOST_UID:HOST_GID` aos arquivos), `no-new-privileges`, `tmpfs /tmp`, 128 MB e 0,25 CPU. Só stdlib (`http.client` sobre o socket unix).

## Coexistência com `./clipar`

O vigia decide **a cada ciclo** se fica ocioso: com `CLIPES_VIGIA_HOST=1` (exportada pelo `./clipar` e repassada ao `vigia`) ou quando o `$OUT/.temp` foi gravado por outro escritor nos últimos 15 s (mtime recente que não é de uma gravação dele; ele guarda mtime/ino da última escrita própria). Ocioso, ele só loga, não escreve nada nem acessa o socket, e despausa o que ele mesmo pausou. Quando o outro escritor para (15 s sem gravar), volta a ativo sozinho. Não precisa reiniciar nem recriar o serviço ao iniciar ou parar o `./clipar`.

## Modelo de confiança

O vigia lê `.vigia` e `.recursos` de `/out`, que o serviço `clipes` (que baixa conteúdo da internet) consegue escrever. Um `clipes` comprometido pode desligar a pausa térmica ou remover o próprio limite (`*`). Isso é por design (o painel é a fonte da config) e o dano fica no Docker do próprio PC. Por isso o vigia lê esses arquivos de forma defensiva: só arquivo regular, sem seguir symlink, sem travar em FIFO, no máximo 256 bytes, e grava com nome temporário aleatório (`O_EXCL|O_NOFOLLOW`).

O projeto compose é fixo (`clipes-lives`): dois checkouts do repositório compartilham o mesmo `vigia` e os mesmos contêineres.

## Teste manual

```sh
docker compose build
docker compose run --rm clipes servico   # sobe o vigia junto
docker compose logs vigia                 # "recursos aplicados ...", falhas e avisos
cat out/.temp
```

No Windows: abra o LibreHardwareMonitor como administrador com **Options → Remote Web Server → Run** (porta 8085) e confira `http://localhost:8085/data.json` no navegador. O Docker Desktop resolve `host.docker.internal` sozinho. Se o LHM usar outra porta, defina `CLIPES_LHM_URL` no `.env`.
