window.paypal
  .Buttons({
    style: {
      shape: "rect",
      layout: "vertical",
      color: "gold",
      label: "paypal",
    },

    async createOrder() {
      let cartProducts = document.getElementById("cartProducts").value;

      cartProducts = JSON.parse(cartProducts)
      console.log("Cart Products: ")
      console.log(cartProducts)
      try {
        const response = await fetch("/api/orders", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            cart: cartProducts,
          }),
        });

        const orderData = await response.json();

        console.log("Order Data: " + orderData);
        if (orderData.id) {
          return orderData.id;
        }
        const errorDetail = orderData?.details?.[0];
        const errorMessage = errorDetail
          ? `${errorDetail.issue} ${errorDetail.description} (${orderData.debug_id})`
          : JSON.stringify(orderData);

        throw new Error(errorMessage);
      } catch (error) {
        console.error(error);
      }
    },

    async onApprove(data, actions) {
      try {
        const response = await fetch(`/api/orders/${data.orderID}/capture`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
        });

        const orderData = await response.json();

        const errorDetail = orderData?.details?.[0];
        console.log("Error detail: " + errorDetail)
        if (errorDetail?.issue === "INSTRUMENT_DECLINED") {
          return actions.restart();
        } else if (errorDetail) {
          throw new Error(`${errorDetail.description} (${orderData.debug_id})`);
        } else if (!orderData.purchase_units) {
          throw new Error(JSON.stringify(orderData));
        } else {
          const transaction =
            orderData?.purchase_units?.[0]?.payments?.captures?.[0] ||
            orderData?.purchase_units?.[0]?.payments?.authorizations?.[0];

          window.location.href = `/checkout/Paypal/${orderData.id}/${transaction.id}`;
        }
      } catch (error) {
        console.log("Error: " + `${error}`)
      }
    },
  })
  .render("#paypal-button-container"); 
