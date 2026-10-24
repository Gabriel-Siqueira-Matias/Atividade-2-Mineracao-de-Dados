# **Mercatto Varejo — Registro de Decisões de Transformação (RDT)**

**RDT-01 a RDT-13  \|  SEMANAS 2 E 3**

**Equipe:** Gabriel Siqueira Matias (2023278440020); Krystyan Douglas Santos Costa (2023278440026); Pedro Alexandre Souza de Campos (2023278440034)

**Disciplina:** Mineração de Dados — IFMT

**Data de consolidação:** 23/10/2026

**Versão do pipeline:** 02\_transformacao.py \+ 03\_carga\_dw.py

**Repositorio do github:**

https\://github.com/Gabriel-Siqueira-Matias/Atividade-2-Mineracao-de-Dados

# **Finalidade e critérios do registro**

Este documento reúne as fichas obrigatórias RDT-01 a RDT-13, respeitando o padrão do professor: evidência quantificada; alternativas; decisão; justificativa; informação perdida; quantidade e percentual afetados; e asserção ou conferência associada.

Os resultados observados durante a execução atual são distinguidos das auditorias anteriores e de decisões ainda não implementadas. As RDT-14 e 15 pertencem à Semana 4 e não foram acrescentadas.

Correção importante de índice: RDT-06 documenta frete negativo e desconto −1; RDT-09 documenta fuso horário, conforme o modelo oficial. A versão anterior do grupo usava outros assuntos nesses números.

# **Resumo da execução verificada**

| Indicador | Resultado |
| :---- | :---- |
| dim\_cliente / dim\_produto | 115.674 / 15.000 |
| dim\_status / dim\_canal | 9 / 3 |
| fato\_pedido / fato\_item\_pedido | 410.000 / 1.371.876 |
| fato\_atendimento / fato\_avaliacao | 95.000 / 160.000 |
| Pedidos recuperados por CPF | 4.290 / 4.290; 0 divergências |
| Asserções Etapas 02 e 03 | 4 / 4 \+ 4 / 4 aprovadas |
| Recarga e conferência completas | 39,7 s |

# **Fichas — RDT-01 a RDT-13**

# 

# **RDT-01  \|  Carga completa ou incremental**

**Etapa do pipeline:** 01\_extracao.py — extrai\_mysql, extrai\_postgres, extrai\_sac, extrai\_avaliacoes

**O que o dado mostrava:** As fontes não disponibilizam CDC ou uma coluna de última alteração confiável e uniforme. Na extração observada foram lidos 577.336 registros ERP, 1.372.388 de e-commerce, 96.140 SAC e 160.000 avaliações: 2.205.864 registros distribuídos entre as quatro fontes.

**Alternativas consideradas:**

**1\. Carga incremental baseada em data/CDC.** menor tráfego, mas detecção de alterações e exclusões incompleta com as informações disponíveis.

**2\. Carga completa em lotes.** reconstrói o staging de forma verificável, ao custo de reler registros inalterados.

**Decisão:** Alternativa 2: extração completa; substituição da tabela na primeira gravação e anexação dos blocos seguintes.

**Justificativa:** As fontes são finitas no laboratório e a recuperação integral simplifica a reexecução e a auditoria. A leitura em blocos limita o uso de RAM.

**O que se perde:** Não permanece no staging um histórico de versões anteriores; paga-se o custo da releitura integral. Não há descarte intencional dos registros de origem.

**Registros afetados:** 2.205.864/2.205.864 linhas reextraídas (100%); descarte deliberado de linhas na extração: 0 (0%).

**Asserção associada:** Contagens origem × stg para cada tabela; QA da extração. Esses controles não medem alterações históricas entre execuções.

# **RDT-02  \|  Unificação de clientes entre ERP e e-commerce**

**Etapa do pipeline:** 02\_transformacao.py — unifica\_clientes

**O que o dado mostrava:** O ERP possui 47.250 cadastros e o e-commerce 85.000 (132.250 entradas). No diagnóstico, foram encontrados 11.470 CPFs válidos distintos em comum; 4.423 CPFs vazios no ERP e 6.855 no e-commerce, além de CPFs com dígitos verificadores inválidos. A execução atual informou 115.674 clientes na dimensão.

**Alternativas consideradas:**

**1\. Unir somente por CPF textual.** parearia números mascarados, inválidos ou compartilhados indevidamente.

**2\. CPF válido, seguido de nome/cidade/UF de correspondência 1:1.** aumenta a cobertura com uma regra mais conservadora para casos sem CPF confiável.

**3\. Não unificar.** evita falsos positivos, mas fragmenta o histórico do mesmo consumidor.

**Decisão:** Alternativa 2: deduplicar CPFs válidos, unir cadastros compatíveis e aplicar pareamento nome/localidade somente em relações 1:1; demais cadastros permanecem separados.

**Justificativa:** Prioriza não fundir homônimos e permite representar consumidores entre sistemas. Cada dim\_cliente tem um cliente\_sk; a procedência é marcada por LEGADO, ECOM ou AMBOS.

**O que se perde:** A similaridade textual não garante identidade e pode deixar um mesmo cliente com várias linhas (falso negativo), ou unir homônimos exatos (falso positivo). A linha da dimensão não preserva todos os IDs secundários descartados.

**Registros afetados:** 132.250 cadastros de entrada (100%); dimensão da execução atual: 115.674 linhas — redução líquida de 16.576 linhas (12,53%), não equivalendo automaticamente a 16.576 pessoas duplicadas.

**Asserção associada:** 02: “CPF valido sem duplicidade” e “origem\_unificacao no dominio”, ambas aprovadas; “pedidos x clientes sem multiplicacao” é controle complementar.

**Nota de transparência:** O diagnóstico conservador anterior sugeria 119.480 clientes; esse valor veio de uma simulação/implementação diferente e não substitui o total observado na execução atual.

# **RDT-03  \|  Duplicatas internas de clientes do legado**

**Etapa do pipeline:** 02\_transformacao.py — unifica\_clientes

**O que o dado mostrava:** No legado foram identificados 1.302 CPFs válidos repetidos, envolvendo 2.604 cadastros. O diagnóstico informa nomes normalizados consistentes nos grupos repetidos; existem IDs diferentes para o mesmo CPF.

**Alternativas consideradas:**

**1\. Manter todos os cadastros na dimensão.** preserva cada ID, mas fragmenta frequência e recorrência de um consumidor.

**2\. Deduplicar por CPF válido, mantendo a primeira linha.** gera um cadastro representativo por CPF, mas perde o ID secundário como coluna direta da dimensão.

**Decisão:** Alternativa 2: ordenar por ID de origem e manter o primeiro registro com CPF válido por grupo; recuperar SK dos pedidos por CPF normalizado quando o ID não estiver na dimensão.

**Justificativa:** A unificação evita duplicação de pessoas na métrica de clientes; a correção posterior de pedidos impede o vínculo arbitrário com o primeiro cliente da dimensão.

**O que se perde:** O ID secundário deixa de constar na linha representativa de dim\_cliente; é recuperável no staging, mas a dimensão não contém um mapa completo persistido de todos os IDs originais.

**Registros afetados:** 2.604/47.250 cadastros (5,51%) participam dos 1.302 grupos repetidos; 1.302 IDs excedentes (2,76% do legado) não são retidos como ID principal desses grupos.

**Asserção associada:** 02: “CPF valido sem duplicidade” (aprovada). Auditoria complementar da RDT-12 comprovou a recuperação de pedidos por CPF, não a permanência de todos os IDs.

# **RDT-04  \|  Conformação dos status, inclusive payment\_declined**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_status

**O que o dado mostrava:** Quatro status distintos no ERP e cinco no e-commerce formam nove pares (sistema, status). O diagnóstico cobre 410.000 pedidos; payment\_declined aparece em 13.990 pedidos ECOM (5,00% do ECOM).

**Alternativas consideradas:**

**1\. Apenas renomear as strings.** mantém categorias inconciliáveis entre sistemas e mistura estados operacionais.

**2\. Dimensão de status com categoria geral e motivo.** permite comparação de processos e conserva a origem e o motivo quando conhecido.

**Decisão:** Alternativa 2: popular nove linhas em dim\_status, incluindo payment\_declined → CANCELADO / RECUSA\_PAGAMENTO; os demais estados são agrupados em CONCLUIDO, CANCELADO, EM\_ANDAMENTO ou EM\_PROCESSAMENTO.

**Justificativa:** Pagamento recusado é um tipo de cancelamento de pedido distinto do cancelamento comum. Reter o status\_origem ajuda a explicar o agrupamento.

**O que se perde:** Ao agregar só status\_conformado, perde-se a especificidade operacional. O motivo CANCELADO\_CLIENTE atribuído a canceled ECOM no código deve ser tratado com cautela, pois a string canceled isolada não comprova iniciativa do cliente.

**Registros afetados:** 410.000/410.000 pedidos (100%) cobertos por nove regras; 13.990 payment\_declined (3,41% de todos os pedidos).

**Asserção associada:** Conferência de nove pares distintos e zero status sem regra nas auditorias; a Etapa 3 testa FK válida de fato\_pedido e a contagem total de pedidos.

# **RDT-05  \|  Semântica dos totais do ERP e do e-commerce**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores**s**

**O que o dado mostrava:** O ERP informa VALOR\_TOTAL já com frete: soma R\$ 281.084.957,13. No e-commerce, total\_amount exclui frete: soma R\$ 734.859.099,95; frete R\$ 10.991.760,38, portanto total padronizado R\$ 745.850.860,33. No ERP havia 4.003 divergências antes do tratamento de desconto-sentinela e zero depois.

**Alternativas consideradas:**

**1\. Usar os totais brutos como comparáveis.** trataria valores com semânticas diferentes como se fossem equivalentes.

**2\. Recompor por itens, descontos e frete conforme.** cria uma medida comum: mercadoria \+ frete − desconto, conciliada com a origem normalizada.

**Decisão:** Alternativa 2: valor\_total \= valor\_mercadoria \+ valor\_frete − valor\_desconto; para conferir ECOM, adicionar o frete a total\_amount da origem; no ERP não somá-lo novamente.

**Justificativa:** A fórmula comum evita superestimar um sistema e subestimar outro por diferença na representação do frete.

**O que se perde:** A medida padronizada não mostra diretamente a semântica particular do total bruto. Reconstituir o valor original exige consultar o staging e a regra aplicada.

**Registros afetados:** 410.000 pedidos (100%) têm valor padronizado; 4.003/130.000 pedidos ERP (3,08%) divergiam antes da correção da sentinela e 0 depois, segundo o diagnóstico.

**Asserção associada:** 02: “pedidos divergentes de itens (\> R\$0,01)” \= 0; 03: “reconciliacao financeira (LEGADO e ECOM, ±0,1%)” \= 0 origens divergentes.

# **RDT-06  \|  Frete negativo e desconto sentinela −1**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores; 03\_carga\_dw.py — carrega\_fato\_item

**O que o dado mostrava:** Foram registrados 362 pedidos ERP e 812 pedidos ECOM com frete negativo (total 1.174). No ERP, 4.056 dos 397.086 itens possuem VL\_DESCONTO \= −1, distribuídos em 4.003 pedidos. Não se encontrou outro desconto negativo no diagnóstico das origens.

**Alternativas consideradas:**

**1\. Manter os sinais como valores financeiros literais.** preserva o bruto, porém fretes negativos e desconto −1 podem contaminar a soma sem regra definida.

**2\. Padronizar frete por valor absoluto e substituir desconto −1 por zero no cálculo.** restaura as conciliações, mas interpreta uma anomalia como valor financeiro não negativo.

**3\. Separar status desconhecido/estorno em campos auxiliares.** preserva a anomalia explicitamente, exigindo modelagem adicional.

**Decisão:** Alternativa 2 na implementação atual: abs(frete) e desconto −1 → 0 nas medidas derivadas; staging permanece inalterado.

**Justificativa:** A soma financeira fechou após tratar a sentinela. A escolha de abs(frete) operacionaliza a comparação, mas NÃO prova que o sinal negativo signifique mero erro: pode representar crédito/estorno e merece revisão posterior.

**O que se perde:** Perde-se o sinal original do frete e a distinção entre desconto realmente zero e sentinela desconhecida na fato (o stg conserva ambos). Não existe indicador específico de anomalia nos campos atuais.

**Registros afetados:** 1.174/410.000 pedidos com frete negativo (0,29%); 4.056/397.086 itens ERP com sentinela (1,02%), atingindo 4.003/130.000 pedidos ERP (3,08%).

**Asserção associada:** Conferência financeira por sistema (03, tolerância ±0,1%): aprovada. Contagens de frete negativo e sentinelas são diagnósticos complementares, não asserções oficiais independentes.

# **RDT-07  \|  Categorias de produto e casos ambíguos**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_categorias

**O que o dado mostrava:** Há 3.000 produtos ERP com rótulos livres e 12.000 produtos ECOM associados a taxonomia de 21 categorias. A auditoria específica ERP encontrou 661 rótulos ambíguos ou não informativos (321 ambíguos e 340 não informativos), enquanto os 12.000 produtos ECOM tinham referências válidas.

**Alternativas consideradas:**

**1\. Manter somente categorias originais.** preserva nuance, mas impede comparações diretas entre os sistemas.

**2\. Agrupar por regras em categoria\_nivel1 e classe residual OUTROS.** permite análise comum, com perda de precisão e possíveis agrupamentos indevidos.

**3\. Deixar categoria ausente quando ambígua.** evita atribuição arbitrária, mas aumenta nulos nas análises por categoria.

**Decisão:** Alternativa 2 na implementação corrente: classificar por palavras-chave (eletro, info, games, telefonia etc.) e colocar não reconhecidos em OUTROS; as categorias originais permanecem no staging.

**Justificativa:** Uma categoria de nível 1 é adequada para analisar sortimento no DW; a classe OUTROS evita imputar uma subcategoria específica que a fonte não garante.

**O que se perde:** Perde-se a subcategoria detalhada na visão agregada e rótulos distintos podem ser fundidos. Atenção: 661 produtos ambíguos/não informativos foram identificados por diagnóstico anterior; a regra final por substring não demonstra tratar esses 661 de modo idêntico à simulação conservadora.

**Registros afetados:** 15.000/15.000 produtos (100%) carregados na dimensão; 661/3.000 produtos ERP (22,03%) merecem cautela por categoria ambígua ou não informativa no diagnóstico.

**Asserção associada:** Conferência de 15.000 produtos carregados; auditoria da taxonomia verificou 21 categorias de referência e 12.000 produtos ECOM válidos. A Etapa 3 confere dim\_produto não vazia, mas não testa semanticamente a classificação.

# **RDT-08  \|  Ausências, datas invertidas e sentinelas**

**Etapa do pipeline:** 02\_transformacao.py — trata\_datas\_e\_ausencias; 03\_carga\_dw.py — carrega\_fatos\_auxiliares

**O que o dado mostrava:** Nos 130.000 pedidos ERP: 28.808 entregas nulas, 1.025 com sentinela 1900-01-01 e 775 entregas anteriores ao pedido (excluída a sentinela). Nos 280.000 ECOM: 72.796 entregas nulas e 1.683 invertidas. No SAC: 5.856 fechamentos e 8.682 notas ausentes. No marketplace: 4.831 avaliações sem campo rating.

**Alternativas consideradas:**

**1\. Substituir ausências por datas/notas artificiais.** produz prazos e opiniões não observados, gerando falsa certeza.

**2\. Preservar NULL e anular datas comprovadamente inválidas.** não inventa informação e mantém contagem de pedidos, embora impeça algumas métricas.

**3\. Excluir os pedidos incompletos.** reduz viés por dados artificiais, mas descarta compras válidas.

**Decisão:** Alternativa 2: entregas anteriores ao pedido ou anteriores a 2000 tornam-se NULL em dw.fato\_pedido; dias\_entrega só é calculado para datas coerentes. Em SAC, fechamento ou satisfação ausente permanecem NULL; rating ausente não vira nota zero.

**Justificativa:** Ausência de entrega pode significar pedido ainda não entregue; sentinelas e inversões impedem cálculo confiável de prazo. É melhor não produzir um número fictício.

**O que se perde:** Perdem-se prazos calculáveis para pedidos sem data válida; não é possível distinguir todas as causas das ausências sem indicadores adicionais. O valor inválido original só permanece no stg.

**Registros afetados:** 101.604/410.000 pedidos (24,78%) sem data de entrega; 3.483/410.000 (0,85%) com data-sentinela ou invertida. SAC: 5.856/96.140 (6,09%) sem fechamento e 8.682/96.140 (9,03%) sem nota. Avaliações: 4.831/160.000 (3,02%) sem rating.

**Asserção associada:** 03: “nenhum pedido com dias\_entrega negativo” \= 0\. Os diagnósticos anteriores conferem as classes de ausência; as quatro asserções atuais não medem separadamente nulos do SAC e marketplace.

# **RDT-09  \|  Padronização e limites de fuso horário**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores / trata\_datas\_e\_ausencias; 03\_carga\_dw.py — carrega\_fato\_avaliacao

**O que o dado mostrava:** ERP usa timestamp sem fuso; e-commerce usa timestamp com fuso. A comparação da data civil em UTC versus America/Cuiaba alteraria 46.530 datas de pedido e 34.621 de entrega do e-commerce. Avaliações: 128.085 timestamps com fuso explícito e 31.915 sem fuso (160.000 no total).

**Alternativas consideradas:**

**1\. Ignorar offset e truncar sempre os primeiros dez caracteres.** simples, mas pode representar o dia local em vez do dia UTC.

**2\. Converter timestamps com offset para UTC, sem atribuir fuso a valores naïve.** explicita instantes comparáveis e reconhece incerteza do legado/NDJSON sem fuso.

**3\. Assumir automaticamente America/Cuiaba para todas as fontes.** altera instantes sem evidência de que todos os sistemas operavam no mesmo fuso.

**Decisão:** Política analítica recomendada: alternativa 2\. O código da transformação usa conversão UTC em parte da geração de datas, mas o carregamento atual de avaliações extrai LEFT(submitted\_at, 10), preservando a data escrita, sem conversão temporal uniforme. Portanto, a padronização total AINDA NÃO ESTÁ DEMONSTRADA pela execução.

**Justificativa:** O diagnóstico estabelece a diferença entre timestamp com e sem fuso; não é legítimo imputar offset aos 31.915 registros sem essa informação. O registro explicita a limitação da versão atual, em vez de afirmar uma normalização universal inexistente.

**O que se perde:** Ao reduzir um timestamp a DATE, perdem-se hora e offset; a data civil pode variar conforme o fuso escolhido. Para campos sem fuso, a conversão exata de instantes não pode ser comprovada.

**Registros afetados:** E-commerce: 46.530/280.000 pedidos (16,62%) e 34.621/280.000 entregas (12,36%) teriam dia diferente entre UTC e Cuiabá; avaliações: 128.085/160.000 (80,05%) com offset e 31.915/160.000 (19,95%) sem offset explícito.

**Asserção associada:** Conferência diagnóstica de 160.000 avaliações com/sem fuso e dos tipos temporais ERP/ECOM. Não existe, nas quatro asserções oficiais atuais, teste que assegure equivalência completa de fuso em todas as fatos.

**Nota de transparência:** PENDÊNCIA TÉCNICA DOCUMENTADA: a regra integral de UTC deve ser concluída se a disciplina exigir timestamps comparáveis e não apenas datas civis em fatos.

# **RDT-10  \|  Itens de pedido órfãos**

**Etapa do pipeline:** 03\_carga\_dw.py — carrega\_fato\_item

**O que o dado mostrava:** A origem apresenta 397.086 itens ERP e 995.367 ECOM: 1.392.453 itens brutos. Desse universo, 5.868 ERP e 14.709 ECOM não encontram pedido na fonte de pedidos. A execução posterior carregou 1.371.876 linhas em dw.fato\_item\_pedido.

**Alternativas consideradas:**

**1\. Inserir todos os itens com pedido\_sk nulo.** incompatível com a FK/NOT NULL da fato de itens.

**2\. Criar pedidos fictícios para órfãos.** preservaria linhas na fato, mas fabricaria compras.

**3\. Manter os órfãos no stg e carregar apenas vinculáveis.** protege a integridade da fato e conserva evidência bruta para investigação.

**Decisão:** Alternativa 3: 20.577 itens sem pedido não entram em fato\_item\_pedido, mas permanecem preservados no staging. A junção com produto também é validada por contagem de elegíveis.

**Justificativa:** Os pedidos do DW só devem receber itens cuja origem seja comprovável. A retenção no staging permite reprocessar se a fonte for corrigida.

**O que se perde:** Os itens órfãos não entram nas análises de itens no DW, podendo subestimar as quantidades brutas de produtos; não são apagados da origem nem do staging.

**Registros afetados:** 20.577/1.392.453 itens (1,48%) não carregados; 5.868/397.086 ERP (1,48%) e 14.709/995.367 ECOM (1,48%). 1.371.876/1.392.453 (98,52%) foram carregados.

**Asserção associada:** Validação específica do script de carga: contagem dos órfãos e comparação do número inserido com itens elegíveis. As quatro asserções oficiais de Etapa 3 não substituem essa validação.

# **RDT-11  \|  Grão de fato\_pedido e fato\_item\_pedido**

**Etapa do pipeline:** 03\_carga\_dw.py — carrega\_fato\_pedido / carrega\_fato\_item

**O que o dado mostrava:** Os pedidos de origem são 130.000 ERP \+ 280.000 ECOM \= 410.000. As fontes de item possuem 1.392.453 registros, dos quais 1.371.876 têm pedido vinculável. Uma junção de pedidos a itens multiplicaria medidas de frete ou total se o grão fosse confundido.

**Alternativas consideradas:**

**1\. Uma única fato detalhada por item, repetindo o total do pedido.** duplicaria totais ao somar os itens.

**2\. Fatos distintas no grão de pedido e no de linha de item.** separa medidas próprias de cada entidade e liga a linha de item ao pedido por pedido\_sk.

**Decisão:** Alternativa 2\. Declarações exigidas: “Uma linha de dw.fato\_pedido representa um pedido individual de um sistema de origem.” “Uma linha de dw.fato\_item\_pedido representa uma linha de item de pedido vinculável, referente a um produto.”

**Justificativa:** A dimensão de produto participa da fato de item; valor\_total e frete do pedido não são repetidos como totais em cada linha de produto.

**O que se perde:** Na fato de itens, o ID natural de linha de origem não foi persistido, limitando a reconciliação linha a linha sem voltar a stg; itens órfãos são excluídos conforme RDT-10.

**Registros afetados:** 410.000/410.000 pedidos (100%); 1.371.876/1.392.453 itens (98,52%) no DW; 20.577 itens (1,48%) fora da fato por ausência de pedido.

**Asserção associada:** 03: “contagem total de fato\_pedido” \= 410.000; validação adicional da contagem de itens elegíveis \= 1.371.876.

# **RDT-12  \|  Pedidos sem cliente correspondente e recuperação por CPF**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores (correção); 03\_carga\_dw.py — recarga de fatos auxiliares

**O que o dado mostrava:** A auditoria do DW encontrou 3.930 pedidos LEGADO e 360 ECOM sem correspondência pelo ID original na dimensão (4.290/410.000). Todos os cadastros existem nas fontes. Na versão anterior, os 4.290 pedidos estavam atribuídos ao primeiro cliente real da dimensão (cliente\_sk=1). A consulta por CPF identificou candidatos para os 4.290; a transformação corrigida informou 3.930/3.930 e 360/360 recuperados.

**Alternativas consideradas:**

**1\. Descartar os pedidos sem mapeamento direto.** reduziria históricos e alteraria as métricas financeiras.

**2\. Preencher cliente\_sk com o primeiro cliente real da dimensão.** evita FK nula, mas imputa milhares de compras à pessoa errada; foi a regra anterior, revogada.

**3\. Recuperar SK pelo CPF de 11 dígitos único na dimensão, mantendo preferência pelo ID.** preserva compras sem criar associações arbitrárias; falha se não há candidato inequívoco.

**Decisão:** Alternativa 3 IMPLEMENTADA. Primeiro vínculo por id\_legado/id\_ecommerce; em falha, CPF extraído do cadastro original e normalizado, aceito somente com 11 dígitos e SK único. Se persistir a falta de candidato, interromper a transformação; não reaplicar fallback arbitrário.

**Justificativa:** A dim\_cliente manteve 115.674 linhas e a fato preservou os 410.000 pedidos. A correção foi reexecutada, seguida da carga da Semana 3\. Consulta pós-carga mostrou LEGADO 3.930/3.930, ECOM 360/360 e zero divergências frente ao CPF único da dimensão.

**O que se perde:** Os IDs secundários ainda não estão todos materializados na linha da dimensão (continua necessário consultar stg para recompor a linhagem). CPF com 11 dígitos único na dimensão não prova, sozinho, identidade real nem validade dos dígitos verificadores; há risco residual a registrar.

**Registros afetados:** 4.290/410.000 pedidos (1,05%) recuperados: 3.930/130.000 LEGADO (3,02%) e 360/280.000 ECOM (0,13%). Identificadores distintos afetados: 1.326 LEGADO e 103 ECOM (1.429 IDs, não necessariamente pessoas distintas). Divergências pós-correção na auditoria: 0/4.290 (0%).

**Asserção associada:** 02: quatro asserções passaram, incluindo ausência de multiplicação (410.000) e CPF válido sem duplicidade. Auditoria ADICIONAL específica confirmou 4.290 associações via CPF único, com zero SK divergentes; não é uma das quatro asserções oficiais.

**Nota de transparência:** A correção evita o fallback arbitrário. Ela ainda deve ser documentada como pareamento técnico condicionado, não como prova jurídica de identidade do consumidor.

# **RDT-13  \|  Atendimentos SAC e avaliações sem vínculo com pedido**

**Etapa do pipeline:** 02\_transformacao.py — liga\_sac\_e\_avaliacoes (diagnóstico); 03\_carga\_dw.py — carrega\_fatos\_auxiliares

**O que o dado mostrava:** O staging possui 96.140 linhas SAC, 95.000 protocolos distintos e 160.000 avaliações com review\_id único. A execução carregou 95.000 atendimentos e 160.000 avaliações. A consulta pós-carga contou 95.000/95.000 atendimentos com pedido\_sk e 160.000/160.000 avaliações vinculadas, zero sem pedido.

**Alternativas consideradas:**

**1\. Excluir registros que não encontrem pedido.** perderia histórico legítimo de satisfação ou atendimento.

**2\. LEFT JOIN pela referência composta, mantendo nulo quando faltar pedido.** preserva o fato auxiliar sem fabricar associação.

**3\. Criar pedido sintético.** preserva FK preenchida, mas introduz um pedido inexistente.

**Decisão:** Alternativa 2 IMPLEMENTADA: SAC localiza o pedido com o sistema \+ ID (LEG- ou ECOM-), consolida em um protocolo por linha; avaliações ligam order\_ref a id\_origem. A referência à fato de pedido tem prioridade sobre um pareamento pelo CPF do atendimento.

**Justificativa:** A etapa preserva informação quando a referência está ausente, e o esquema permite pedido\_sk nulo nas fatos auxiliares. Na execução informada, nenhuma referência ficou sem pedido.

**O que se perde:** A consolidação por protocolo não transporta todas as 1.140 linhas adicionais do SAC para a fato; permanecem em stg. O CPF do SAC pode divergir do cadastro do pedido (diagnóstico anterior indicou 88.011 divergências entre 88.012 comparáveis), sem que isso justifique mudar automaticamente o pedido vinculado. A presença de SKU não comprova que o produto era item daquele pedido.

**Registros afetados:** SAC: 95.000/95.000 protocolos ligados (100%), 0 sem pedido; 1.140/96.140 linhas brutas (1,19%) não repetidas na fato. Marketplace: 160.000/160.000 reviews ligados (100%), 0 sem pedido.

**Asserção associada:** Auditoria automática após o COMMIT: auxiliares sem pedido (SAC=0, avaliações=0), contagens 95.000 e 160.000 e 4/4 asserções da carga aprovadas. Essa conferência de vínculo é complementar ao TODO 3.5.

# **Tabela-resumo das RDT-01 a RDT-13**

| Ficha | Decisão | Registros afetados | % da base | Informação perdida? |
| :---- | :---- | :---- | :---- | :---- |
| RDT-01 | Carga completa | 2.205.864 | 100% | Histórico prévio do staging |
| RDT-02 | CPF válido \+ nome/localidade 1:1 | 132.250 cadastros; 115.674 dimensões | 100% / redução 12,53% | Risco residual de pareamento |
| RDT-03 | Deduplicar CPFs válidos ERP | 2.604 cadastros; 1.302 IDs excedentes | 5,51% / 2,76% ERP | IDs secundários na dimensão |
| RDT-04 | Nove status conformados | 410.000 pedidos | 100% | Especificidade operacional agregada |
| RDT-05 | Normalização do total financeiro | 410.000 pedidos; 4.003 divergências tratadas | 100% / 3,08% ERP | Semântica bruta do total na fato |
| RDT-06 | abs(frete); sentinela −1 → 0 | 1.174 fretes; 4.056 itens ERP | 0,29% pedidos / 1,02% itens ERP | Sinal do frete e código sentinela |
| RDT-07 | Categoria nível 1/OUTROS | 15.000 produtos; 661 ERP ambíguos | 100% / 22,03% ERP | Granularidade da categoria |
| RDT-08 | NULL para ausência/data inválida | 101.604 ausências; 3.483 datas inválidas | 24,78% / 0,85% pedidos | Prazos e causas não observados |
| RDT-09 | UTC parcial; fuso indefinido documentado | 46.530 pedidos ECOM; 31.915 reviews sem fuso | 16,62% ECOM / 19,95% reviews | Instante/offset ao reduzir a DATE |
| RDT-10 | Órfãos retidos em stg | 20.577 itens | 1,48% dos itens | Itens ausentes da fato |
| RDT-11 | Grão pedido e item separados | 410.000 pedidos; 1.371.876 itens | 100% / 98,52% | IDs naturais das linhas de item |
| RDT-12 | Vincular ID e recuperar por CPF único | 4.290 pedidos recuperados | 1,05% dos pedidos | ID secundário não persistido na dimensão |
| RDT-13 | LEFT JOIN; consolidar por protocolo | 95.000 SAC \+ 160.000 avaliações; 1.140 linhas extras | 100% vinculados / 1,19% SAC não repetido | Linhas repetidas SAC na fato |

# **Reflexão final**

**1\. Qual decisão mudaria mais o resultado de um modelo?** A unificação da identidade do cliente (RDT-02/03/12). A frequência e a recência de compras seriam distorcidas se registros do mesmo cliente fossem separados ou se clientes diferentes fossem fundidos. O laboratório evidenciou o risco: 4.290 pedidos foram inicialmente imputados a um único cliente real por fallback; após a correção, todos obtiveram vínculo técnico por CPF único. O impacto no desempenho de um modelo de recompra não foi medido nesta etapa e permanece hipótese.

**2\. Que conveniência técnica afetaria pessoas reais?** A imputação do primeiro cliente como substituto para pedidos não identificados poderia atribuir compras a terceiros. Também a classificação de cancelamentos, fretes negativos e ausência de satisfação pode afetar decisões de negócio e reputação se apresentada como verdade sem ressalvas.

**3\. Qual informação fez falta?** Um identificador universal de consumidor entre sistemas, uma trilha persistente de todos os IDs de origem, a causa dos fretes negativos/descontos-sentinela e metadados de fuso para os timestamps sem offset. Essas limitações foram explicitadas em vez de escondidas por imputações.